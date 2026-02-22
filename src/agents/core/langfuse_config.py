"""Langfuse configuration and singleton client.

Provides a single Langfuse instance for use across all agents.
Tracing can be disabled via LANGFUSE_TRACING_ENABLED=false env variable.
"""

import os

from langfuse import Langfuse

from src.agents.core.config import settings


def _create_langfuse_client() -> Langfuse | None:
    """Create Langfuse client if configured and tracing is enabled.
    
    When tracing is disabled, sets LANGFUSE_TRACING_ENABLED in os.environ
    so the SDK's @observe decorator also becomes a no-op (pydantic-settings
    reads .env but does not propagate values to os.environ).
    
    Returns:
        Langfuse client instance, or None if not configured or tracing disabled
    """
    if not settings.langfuse.LANGFUSE_TRACING_ENABLED:
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
        return None

    if not settings.langfuse.LANGFUSE_PUBLIC_KEY or not settings.langfuse.LANGFUSE_SECRET_KEY:
        return None
    
    return Langfuse(
        public_key=settings.langfuse.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.langfuse.LANGFUSE_SECRET_KEY,
        host=settings.langfuse.LANGFUSE_HOST,
    )


# Single global Langfuse instance
# Import this wherever you need Langfuse:
#   from src.agents.core.langfuse_config import langfuse
langfuse = _create_langfuse_client()


def is_langfuse_enabled() -> bool:
    """Check if Langfuse is configured, available, and tracing is enabled."""
    return langfuse is not None
