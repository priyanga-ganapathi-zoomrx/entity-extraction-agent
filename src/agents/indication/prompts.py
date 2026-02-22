"""Prompts for indication extraction and validation agents."""

from pathlib import Path

from src.agents.core import load_prompt


# Prompt names
EXTRACTION_PROMPT_NAME = "MEDICAL_INDICATION_EXTRACTION_SYSTEM_PROMPT"
VALIDATION_PROMPT_NAME = "INDICATION_VALIDATION_SYSTEM_PROMPT"

# Prompts directory
PROMPTS_DIR = Path(__file__).parent / "prompts"


def get_extraction_prompt() -> tuple[str, str]:
    """Load indication extraction prompt."""
    return load_prompt(EXTRACTION_PROMPT_NAME, PROMPTS_DIR)


def get_validation_prompt() -> tuple[str, str]:
    """Load indication validation prompt."""
    return load_prompt(VALIDATION_PROMPT_NAME, PROMPTS_DIR)
