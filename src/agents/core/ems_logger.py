"""EMS (Event Management System) structured logging with Sentry integration.

Provides a unified logger that:
- Formats events as ECS (Elastic Common Schema) JSON via structlog + ecs_logging
- Publishes events to Google Cloud Pub/Sub when EMS_ENABLED=true
- Captures ERROR-level events to Sentry when SENTRY_ENABLED=true
- Exposes a simple ``get_logger(step_name)`` API for callers

Architecture:
    logger.info("step_completed", abstract_id="1356", ...)
      │
      ▼ structlog processor chain:
      │  1. merge_contextvars
      │  2. add_log_level
      │  3. TimeStamper (ISO 8601)
      │  4. _sentry_processor  → if ERROR: sentry_sdk.capture_exception()
      │  5. ecs_logging.StructlogFormatter()  → ECS JSON string
      ▼
    _EmsLogger.msg()  → publishes JSON to Pub/Sub (fire-and-forget)

Usage:
    from src.agents.core.ems_logger import get_logger

    logger = get_logger("drug_extraction")
    logger.info("step_completed", abstract_id="1356", outcome="success", ...)
    logger.error("step_failed", abstract_id="1356", error=str(e), exc_info=True)
"""

import json
import logging
import sys
from dataclasses import asdict, dataclass
from typing import Any

import ecs_logging
import sentry_sdk
import structlog

from src.agents.core.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVICE_NAME = "entity-extraction-agent"

_fallback_logger = logging.getLogger("ems_logger")

# ---------------------------------------------------------------------------
# Sentry initialisation (once at import time)
# ---------------------------------------------------------------------------

_sentry_initialised = False


def _init_sentry() -> None:
    """Initialise Sentry SDK if enabled. Safe to call multiple times."""
    global _sentry_initialised
    if _sentry_initialised:
        return
    _sentry_initialised = True

    if not settings.sentry.SENTRY_ENABLED or not settings.sentry.SENTRY_DSN:
        return

    _MAX_VAR_BYTES = 512

    def _before_send(event, hint):
        """Trim oversized local-variable representations so the envelope
        stays within Sentry's size limit (~200 KB for self-hosted).

        Some exceptions carry large payloads (e.g. Tavily search results)
        in their call-stack variables.  Without trimming the server returns
        HTTP 400 "envelope exceeded size limits".
        """
        for exc_val in event.get("exception", {}).get("values", []):
            for frame in exc_val.get("stacktrace", {}).get("frames", []):
                frame_vars = frame.get("vars")
                if not frame_vars:
                    continue
                for key in list(frame_vars):
                    try:
                        size = len(json.dumps(frame_vars[key], default=str))
                    except Exception:
                        size = len(str(frame_vars[key]))
                    if size > _MAX_VAR_BYTES:
                        short = str(frame_vars[key])[:200]
                        frame_vars[key] = f"{short}…[truncated, was {size}B]"

        return event

    sentry_sdk.init(
        dsn=settings.sentry.SENTRY_DSN,
        environment=settings.sentry.SENTRY_ENVIRONMENT,
        # Disable auto-capture integrations — we capture explicitly
        # via _sentry_processor to avoid double-capture in Temporal workers.
        default_integrations=False,
        traces_sample_rate=0,
        before_send=_before_send,
    )


# ---------------------------------------------------------------------------
# Pub/Sub publisher (singleton)
# ---------------------------------------------------------------------------

_publisher = None
_topic_path: str | None = None


def _get_publisher():
    """Lazily create a singleton ``PublisherClient`` if EMS is enabled.

    Returns ``(publisher, topic_path)`` or ``(None, None)``.
    """
    global _publisher, _topic_path

    if _publisher is not None:
        return _publisher, _topic_path

    if not settings.ems.EMS_ENABLED or not settings.ems.EMS_PUBSUB_TOPIC:
        return None, None

    project_id = settings.gcs.GCS_PROJECT_ID
    if not project_id:
        _fallback_logger.warning(
            "EMS_ENABLED=true but GCS_PROJECT_ID is empty — Pub/Sub publishing disabled"
        )
        return None, None

    try:
        from google.cloud import pubsub_v1  # noqa: E402

        _publisher = pubsub_v1.PublisherClient()
        _topic_path = _publisher.topic_path(project_id, settings.ems.EMS_PUBSUB_TOPIC)
        _fallback_logger.info("Pub/Sub publisher initialised: %s", _topic_path)
    except Exception as exc:
        _fallback_logger.error("Failed to create Pub/Sub publisher: %s", exc)
        return None, None

    return _publisher, _topic_path


# ---------------------------------------------------------------------------
# structlog processors
# ---------------------------------------------------------------------------

def _sentry_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Capture ERROR-level events to Sentry with rich tags.

    Sets Sentry tags for ``step_name``, ``abstract_id``, ``model``,
    ``attempt``, and ``workflow_run_id`` so errors are filterable in
    the Sentry dashboard.
    """
    if not settings.sentry.SENTRY_ENABLED:
        return event_dict

    level = event_dict.get("level", "")
    if level not in ("error", "critical"):
        return event_dict

    # Set Sentry tags for dashboard filtering
    tag_fields = ("step_name", "abstract_id", "model", "attempt", "workflow_run_id")
    for field in tag_fields:
        value = event_dict.get(field)
        if value is not None:
            sentry_sdk.set_tag(field, str(value))

    # Capture the exception if one is active, otherwise send a message
    exc_info = event_dict.get("exc_info")
    if exc_info and exc_info is not True:
        # exc_info is a (type, value, tb) tuple
        sentry_sdk.capture_exception(exc_info)
    elif sys.exc_info()[0] is not None:
        # There is an active exception on the stack
        sentry_sdk.capture_exception()
    else:
        # No exception — send as a message
        sentry_sdk.capture_message(
            event_dict.get("event", "unknown_error"), level="error"
        )

    return event_dict


# ---------------------------------------------------------------------------
# Custom logger class that publishes to Pub/Sub
# ---------------------------------------------------------------------------

class _EmsLogger:
    """A minimal logger backend that publishes rendered ECS JSON to Pub/Sub.

    structlog's ``PrintLoggerFactory`` normally prints to stdout.  We replace
    it with this class so the *final* rendered string (ECS JSON) is sent to
    Pub/Sub fire-and-forget.
    """

    def msg(self, message: str) -> None:
        """Publish a single rendered log line to Pub/Sub."""
        publisher, topic_path = _get_publisher()
        if publisher is None or topic_path is None:
            return

        try:
            data = message.encode("utf-8") if isinstance(message, str) else message
            publisher.publish(topic_path, data=data)
        except Exception as exc:
            # Fallback: log to stderr so container logs capture the failure.
            _fallback_logger.error("Pub/Sub publish failed: %s", exc)
            # Also attempt Sentry capture (won't recurse — this is a direct SDK call)
            try:
                if settings.sentry.SENTRY_ENABLED:
                    sentry_sdk.capture_exception(exc)
            except Exception:
                pass

    # structlog proxies method names matching Python logging levels
    # (debug, info, warning, error, critical) plus its own aliases
    # (msg, err, warn, fatal) to the underlying logger.
    err = msg
    error = msg
    warn = msg
    warning = msg
    info = msg
    debug = msg
    fatal = msg
    critical = msg
    exception = msg


class _EmsLoggerFactory:
    """Factory that returns our custom Pub/Sub logger."""

    def __call__(self, *args: Any, **kwargs: Any) -> _EmsLogger:
        return _EmsLogger()


# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

_configured = False


def _configure_structlog() -> None:
    """Configure the global structlog processor chain (idempotent)."""
    global _configured
    if _configured:
        return
    _configured = True

    _init_sentry()

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _sentry_processor,
        ecs_logging.StructlogFormatter(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=_EmsLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# ECS Log Schemas
# ---------------------------------------------------------------------------

@dataclass
class TransactionLogSchema:
    """Schema for transaction context in ECS format."""
    id: str
    name: str
    congress_id: int = 0
    session_id: str = ""
    session_title: str = ""
    workflow_id: str = ""
    batch_id: int = 0


@dataclass
class LLMLogSchema:
    """Schema for LLM call details in ECS format."""
    model: str
    prompt_file: str = "inline"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class EventDetailsLogSchema:
    """Schema for event details in ECS format."""
    entity: str
    activity: str
    input: dict
    output: dict
    error: str = ""
    attempt: int = 1
    labels: dict = None
    llm_calls: int = 0
    status: str = "success"
    duration: int = 0

    def __post_init__(self):
        if self.labels is None:
            self.labels = {}


def build_ecs_log_event(
    transaction: TransactionLogSchema,
    event_details: EventDetailsLogSchema,
    llm: LLMLogSchema | None = None,
) -> dict[str, Any]:
    """Build a nested ECS log event from component schemas.

    Args:
        transaction: Transaction context (id, congress_id, batch_id, etc.)
        event_details: Event details (entity, activity, input, output, status, etc.)
        llm: LLM call details (model, tokens, prompt_file) - optional

    Returns:
        Nested dict ready for structured logging with transaction, event_details, and llm fields.
    """
    log_event = {
        "transaction": asdict(transaction),
        "event_details": asdict(event_details),
    }

    if llm is not None:
        log_event["llm"] = asdict(llm)

    # Serialize labels dict to JSON string for storage
    if event_details.labels:
        log_event["event_details"]["labels"] = json.dumps(event_details.labels)
    else:
        log_event["event_details"]["labels"] = "{}"

    return log_event


# ---------------------------------------------------------------------------
# ActivityLogger - Smart logging wrapper
# ---------------------------------------------------------------------------

class ActivityLogger:
    """Smart logger wrapper that handles ECS log event building automatically.

    Encapsulates all the complexity of building nested ECS log events so activity
    code stays clean and focused on business logic.

    Example usage:
        from src.agents.core.ems_logger import ActivityLogger

        # Initialize once at start of activity
        activity_logger = ActivityLogger(
            step_name="drug_extraction",
            entity="drug",
            activity="extract",
            input_data=input_data,  # Must have: abstract_id, abstract_title, congress_id, batch_id
            model="gpt-4",
            prompt_file="inline",
        )

        # Log success with one line
        activity_logger.log_success(
            output={"drugs": drugs_list},
            labels={"num_drugs_extracted": len(drugs_list)},
            tracker=token_tracker,
            duration_ms=duration_ms,
        )

        # Or log error with one line
        activity_logger.log_error(
            error=exc,
            labels={"error_type": "validation_failed"},
            tracker=token_tracker,
            duration_ms=duration_ms,
        )
    """

    def __init__(
        self,
        step_name: str,
        entity: str,
        activity: str,
        input_data,
        model: str,
        prompt_file: str = "inline",
    ):
        """Initialize the activity logger.

        Args:
            step_name: Logical step identifier (e.g. "drug_extraction")
            entity: Entity type being processed (e.g. "drug", "indication", "drug_class")
            activity: Activity type (e.g. "extract", "validate", "regimen")
            input_data: Input schema instance (must have abstract_id, abstract_title, congress_id, batch_id)
            model: LLM model name (e.g. "gpt-4")
            prompt_file: Prompt file path (e.g. "gcs://bucket/prompt.txt") or "inline"
        """
        self.step_name = step_name
        self.entity = entity
        self.activity = activity
        self.input_data = input_data
        self.model = model
        self.prompt_file = prompt_file
        self.logger = get_logger(step_name)

    def _build_transaction(self) -> TransactionLogSchema:
        """Build transaction schema from input_data."""
        return TransactionLogSchema(
            id=str(self.input_data.abstract_id),
            name=self.input_data.abstract_title,
            congress_id=getattr(self.input_data, "congress_id", 0),
            session_id="",
            session_title=getattr(self.input_data, "session_title", ""),
            workflow_id="",
            batch_id=getattr(self.input_data, "batch_id", 0),
        )

    def log_success(
        self,
        output: dict,
        labels: dict,
        tracker,
        duration_ms: int,
    ) -> None:
        """Log a successful activity completion.

        Args:
            output: Activity output as dict (e.g. {"drugs": ["aspirin"]})
            labels: Additional metadata labels as dict
            tracker: TokenUsageCallbackHandler instance
            duration_ms: Activity duration in milliseconds
        """
        transaction = self._build_transaction()

        event_details = EventDetailsLogSchema(
            entity=self.entity,
            activity=self.activity,
            input=asdict(self.input_data),
            output=output,
            error="",
            attempt=1,
            labels=labels,
            llm_calls=tracker.llm_calls,
            status="success",
            duration=duration_ms,
        )

        llm = LLMLogSchema(
            model=self.model,
            prompt_file=self.prompt_file,
            input_tokens=tracker.usage.input_tokens,
            output_tokens=tracker.usage.output_tokens,
            total_tokens=tracker.usage.total_tokens,
        )

        log_event = build_ecs_log_event(transaction, event_details, llm)
        self.logger.info("step_completed", **log_event)

    def log_error(
        self,
        error: Exception,
        labels: dict,
        tracker,
        duration_ms: int,
    ) -> None:
        """Log a failed activity execution.

        Args:
            error: Exception that caused the failure
            labels: Additional metadata labels as dict
            tracker: TokenUsageCallbackHandler instance
            duration_ms: Activity duration in milliseconds
        """
        transaction = self._build_transaction()

        event_details = EventDetailsLogSchema(
            entity=self.entity,
            activity=self.activity,
            input=asdict(self.input_data),
            output={},
            error=str(error),
            attempt=1,
            labels=labels,
            llm_calls=tracker.llm_calls,
            status="failed",
            duration=duration_ms,
        )

        llm = LLMLogSchema(
            model=self.model,
            prompt_file=self.prompt_file,
            input_tokens=tracker.usage.input_tokens,
            output_tokens=tracker.usage.output_tokens,
            total_tokens=tracker.usage.total_tokens,
        )

        log_event = build_ecs_log_event(transaction, event_details, llm)
        self.logger.error("step_failed", **log_event, exc_info=True)

    def log_skipped(
        self,
        reason: str,
        duration_ms: int,
    ) -> None:
        """Log a skipped activity (no LLM call made).

        Args:
            reason: Reason for skipping (e.g. "no_primary_drug")
            duration_ms: Activity duration in milliseconds
        """
        transaction = self._build_transaction()

        event_details = EventDetailsLogSchema(
            entity=self.entity,
            activity=self.activity,
            input=asdict(self.input_data),
            output={},
            error="",
            attempt=1,
            labels={"skip_reason": reason},
            llm_calls=0,
            status="skipped",
            duration=duration_ms,
        )

        log_event = build_ecs_log_event(transaction, event_details, llm=None)
        self.logger.info("step_skipped", **log_event)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_logger(step_name: str) -> structlog.BoundLogger:
    """Return a structlog logger pre-bound with ``step_name`` and ``service.name``.

    Args:
        step_name: Logical step identifier (e.g. ``"drug_extraction"``).

    Returns:
        A ``structlog.BoundLogger`` ready for ``.info()`` / ``.error()`` calls.
    """
    _configure_structlog()
    return structlog.get_logger(
        **{
            "step_name": step_name,
            "service.name": SERVICE_NAME,
        }
    )
