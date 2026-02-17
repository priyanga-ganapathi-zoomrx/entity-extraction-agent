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
