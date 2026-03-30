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


def send_teams_message(
    title: str,
    message: str,
    mention: dict | None = None,
) -> None:
    """Send a message to Teams. Never raises — logs/Sentry on failure.

    Args:
        title: Bold header text
        message: Body text (supports **bold** and <br> for line breaks)
        mention: Optional dict with 'name' and 'email' to @mention a user
    """
    url = _get_teams_webhook_url()
    if not url:
        logger.info("Teams webhook URL not configured, skipping notification")
        return

    # Build Adaptive Card body from message text
    body_blocks: list[dict] = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]

    # Split on <br> and --- to create text blocks
    for line in message.split("<br>"):
        line = line.strip()
        if line == "---":
            body_blocks.append({"type": "TextBlock", "text": "───", "spacing": "Small"})
        elif line:
            body_blocks.append({"type": "TextBlock", "text": line, "wrap": True})

    card_content = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body_blocks,
    }

    if mention:
        card_content["msteams"] = {
            "entities": [
                {
                    "type": "mention",
                    "text": f"<at>{mention['name']}</at>",
                    "mentioned": {
                        "id": mention["email"],
                        "name": mention["name"],
                    },
                }
            ]
        }

    card_payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content,
            }
        ],
    }

    try:
        _post_to_teams(url, card_payload)
        logger.info("Teams notification sent successfully")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Failed to send Teams notification", exc_info=True)


def format_facts(data: dict) -> str:
    """Format a dict as Teams message text with bold keys and <br> separators."""
    return "<br>".join(f"**{k}**: {v}" for k, v in data.items())
