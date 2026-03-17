"""Generic prompt loading utilities for all agents.

Loads prompts from GCS bucket (primary) with local file fallback.
Prompts are cached per process lifetime to avoid repeated fetching.
"""

import logging
import os
from pathlib import Path

from google.cloud import storage as gcs_storage

from src.agents.core.config import settings
from src.agents.core.ems_logger import get_logger

logger = logging.getLogger(__name__)
ems_logger = get_logger("prompt_loader")

_prompt_cache: dict[str, tuple[str, str]] = {}


def load_prompt(
    prompt_name: str,
    prompts_dir: Path,
) -> tuple[str, str]:
    """Load prompt from GCS bucket or fallback to local file.

    GCS path convention: prompts/{agent_type}/{prompt_name}.md
    where agent_type is derived from prompts_dir (its parent directory name).

    Args:
        prompt_name: Prompt filename without extension (e.g., "DRUG_EXTRACTION_SYSTEM_PROMPT")
        prompts_dir: Directory containing local prompt files (used for fallback and agent_type derivation)

    Returns:
        tuple[str, str]: (prompt_content, file_path)
            - file_path is "gcs://bucket_name/prompts/{agent_type}/{prompt_name}.md" when loaded from GCS
            - file_path is "inline" when loaded from local file
    """
    if prompt_name in _prompt_cache:
        return _prompt_cache[prompt_name]

    if settings.gcs.GCS_BUCKET_NAME:
        try:
            result = _load_from_gcs(prompt_name, prompts_dir)
            _prompt_cache[prompt_name] = result
            return result
        except Exception as e:
            ems_logger.error("gcs_prompt_load_failed", prompt_name=prompt_name, error=str(e))

    result = _load_from_file(prompt_name, prompts_dir)
    _prompt_cache[prompt_name] = result
    return result


def _load_from_gcs(prompt_name: str, prompts_dir: Path) -> tuple[str, str]:
    """Load prompt from GCS bucket.

    Uses google.cloud.storage directly to access blob metadata.
    """
    if settings.gcs.GOOGLE_APPLICATION_CREDENTIALS:
        creds_path = settings.gcs.GOOGLE_APPLICATION_CREDENTIALS.strip()
        if creds_path and os.path.exists(creds_path):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

    project_id = settings.gcs.GCS_PROJECT_ID.strip() if settings.gcs.GCS_PROJECT_ID else None
    client = gcs_storage.Client(project=project_id) if project_id else gcs_storage.Client()

    bucket_name = settings.gcs.GCS_BUCKET_NAME
    bucket = client.bucket(bucket_name)
    agent_type = prompts_dir.parent.name
    blob_path = f"prompts/{agent_type}/{prompt_name}.md"
    blob = bucket.blob(blob_path)

    content = blob.download_as_text()
    content = content.lstrip("\ufeff").strip()

    # Return GCS URI as file_path for EMS logging
    file_path = f"gcs://{bucket_name}/{blob_path}"

    logger.info("Loaded prompt '%s' from GCS: %s", prompt_name, file_path)
    return content, file_path


def _load_from_file(prompt_name: str, prompts_dir: Path) -> tuple[str, str]:
    """Load prompt from local file.

    Returns 'inline' as file_path to indicate local/embedded prompts.
    """
    prompt_file = prompts_dir / f"{prompt_name}.md"
    logger.info("Loading prompt from %s", prompt_file)
    content = prompt_file.read_text(encoding="utf-8")
    return content.strip(), "inline"


def clear_prompt_cache() -> None:
    """Clear the prompt cache. Useful for testing or after prompt updates."""
    _prompt_cache.clear()
