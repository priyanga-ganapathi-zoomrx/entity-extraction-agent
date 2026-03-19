"""Microsoft Teams notification client.

Pattern replicated from congress-content-utilities/ms_teams.py.
Webhook URL loaded from TEAMS_WEBHOOK_URL environment variable.
"""

import logging
from os import getenv

import httpx
import sentry_sdk
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


def _get_teams_webhook_url() -> str | None:
    """Load Teams webhook URL from environment variable."""
    return getenv("TEAMS_WEBHOOK_URL")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=10, max=40))
def _post_to_teams(url: str, json_data: dict) -> None:
    """POST to Teams webhook with retry (3 attempts, exponential backoff)."""
    resp = httpx.post(url, json=json_data, timeout=10)
    resp.raise_for_status()


def send_teams_message(title: str, message: str) -> None:
    """Send a message to Teams. Never raises — logs/Sentry on failure.

    Args:
        title: Bold header text
        message: Body text (supports **bold** and <br> for line breaks)
    """
    url = _get_teams_webhook_url()
    if not url:
        logger.info("Teams webhook URL not configured, skipping notification")
        return

    try:
        _post_to_teams(url, {"title": title, "text": message})
        logger.info("Teams notification sent successfully")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Failed to send Teams notification", exc_info=True)


def format_facts(data: dict) -> str:
    """Format a dict as Teams message text with bold keys and <br> separators.

    Matches congress-content-utilities pattern: prepare_message_from_dict()
    """
    return "<br>".join(f"**{k}**: {v}" for k, v in data.items())
