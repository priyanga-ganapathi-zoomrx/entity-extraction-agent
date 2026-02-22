"""Prompts module for drug class extraction, validation, and related agents."""

import re
from pathlib import Path

from src.agents.core.prompts import load_prompt


# Available prompt names
EXTRACTION_TITLE_PROMPT_NAME = "DRUG_CLASS_EXTRACTION_FROM_TITLE"
EXTRACTION_RULES_PROMPT_NAME = "DRUG_CLASS_EXTRACTION_FROM_SEARCH_REACT_PATTERN"
VALIDATION_PROMPT_NAME = "DRUG_CLASS_VALIDATION_SYSTEM_PROMPT"
SELECTION_PROMPT_NAME = "DRUG_CLASS_SELECTION_SYSTEM_PROMPT"
GROUNDED_SEARCH_PROMPT_NAME = "DRUG_CLASS_GROUNDED_SEARCH_PROMPT"
CONSOLIDATION_PROMPT_NAME = "DRUG_CLASS_CONSOLIDATION_PROMPT"
REGIMEN_IDENTIFICATION_PROMPT_NAME = "REGIMEN_IDENTIFICATION_PROMPT"

# Default prompts directory (relative to this file)
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Prompt cache to avoid repeated fetching
_prompt_cache: dict[str, tuple[str, str]] = {}


# =============================================================================
# SECTION EXTRACTION HELPER
# =============================================================================

def extract_section(content: str, section_name: str) -> str:
    """Extract content between MESSAGE markers in prompt file.
    
    Args:
        content: Full prompt file content
        section_name: Section name to extract (e.g., "SYSTEM_PROMPT")
        
    Returns:
        Extracted section content, or empty string if not found
    """
    pattern = rf'<!-- MESSAGE_\d+_START: {section_name} -->\s*(.*?)\s*<!-- MESSAGE_\d+_END: {section_name} -->'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        section = match.group(1).strip()
        section = re.sub(rf'^##\s*{section_name}\s*\n+', '', section)
        return section
    return ""


# =============================================================================
# PROMPT LOADING FUNCTIONS
# =============================================================================

def get_system_prompt(
    prompt_name: str = EXTRACTION_TITLE_PROMPT_NAME,
    prompt_dir: Path | None = None,
) -> tuple[str, str]:
    """Load prompt from GCS bucket or local file via core load_prompt().

    Args:
        prompt_name: Name of the prompt (used as filename without extension)
        prompt_dir: Optional directory to look for prompt files (default: prompts/ in same folder)

    Returns:
        tuple[str, str]: A tuple of (prompt_content, prompt_version)
    """
    if prompt_name in _prompt_cache:
        return _prompt_cache[prompt_name]
    
    prompts_directory = prompt_dir or PROMPTS_DIR
    result = load_prompt(prompt_name, prompts_directory)
    _prompt_cache[prompt_name] = result
    return result


def get_extraction_title_prompt() -> tuple[str, str]:
    """Load the drug class extraction from title prompt."""
    return get_system_prompt(prompt_name=EXTRACTION_TITLE_PROMPT_NAME)


def get_extraction_rules_prompt() -> tuple[str, str]:
    """Load the drug class extraction rules prompt."""
    return get_system_prompt(prompt_name=EXTRACTION_RULES_PROMPT_NAME)


def get_validation_prompt() -> tuple[str, str]:
    """Load the drug class validation prompt."""
    return get_system_prompt(prompt_name=VALIDATION_PROMPT_NAME)


def get_selection_prompt() -> tuple[str, str]:
    """Load the drug class selection prompt."""
    return get_system_prompt(prompt_name=SELECTION_PROMPT_NAME)


def get_grounded_search_prompt() -> tuple[str, str]:
    """Load the drug class grounded search prompt."""
    return get_system_prompt(prompt_name=GROUNDED_SEARCH_PROMPT_NAME)


def get_consolidation_prompt() -> tuple[str, str]:
    """Load the drug class consolidation prompt."""
    return get_system_prompt(prompt_name=CONSOLIDATION_PROMPT_NAME)


def get_regimen_identification_prompt() -> tuple[str, str]:
    """Load the regimen identification prompt."""
    return get_system_prompt(prompt_name=REGIMEN_IDENTIFICATION_PROMPT_NAME)


# =============================================================================
# PARSED PROMPT FUNCTIONS (return sections)
# =============================================================================

def get_extraction_rules_prompt_parts() -> tuple[str, str, str]:
    """Load and parse extraction rules prompt into system prompt and rules.
    
    Returns:
        Tuple of (system_prompt, rules_message, version)
    """
    full_prompt, version = get_extraction_rules_prompt()
    
    system_prompt = extract_section(full_prompt, "SYSTEM_PROMPT")
    rules_message = extract_section(full_prompt, "RULES_MESSAGE")
    
    return system_prompt, rules_message, version


def get_grounded_search_prompt_parts() -> tuple[str, str, str]:
    """Load and parse grounded search prompt into system prompt and rules.
    
    Returns:
        Tuple of (system_prompt, rules_message, version)
    """
    full_prompt, version = get_grounded_search_prompt()
    
    system_prompt = extract_section(full_prompt, "SYSTEM_PROMPT")
    rules_message = extract_section(full_prompt, "RULES_MESSAGE")
    
    if not system_prompt:
        system_prompt = full_prompt
        rules_message = ""
    
    return system_prompt, rules_message, version


def get_selection_prompt_parts() -> tuple[str, str, str]:
    """Load selection prompt and extraction rules for selection step.
    
    The selection step uses the selection prompt as system message and
    the extraction rules (RULES_MESSAGE section) to guide selection decisions.
    
    Returns:
        Tuple of (selection_prompt, rules_message, version)
    """
    selection_prompt, selection_version = get_selection_prompt()
    rules_content, _ = get_extraction_rules_prompt()
    
    rules_message = extract_section(rules_content, "RULES_MESSAGE")
    
    return selection_prompt, rules_message, selection_version


def get_explicit_extraction_prompt_parts() -> tuple[str, str, str, str]:
    """Load and parse explicit extraction (from title) prompt into sections.
    
    The explicit extraction step extracts drug classes directly from the
    abstract title. It uses the title extraction prompt and extraction rules.
    
    Returns:
        Tuple of (system_prompt, input_template, rules_message, version)
    """
    title_prompt, title_version = get_extraction_title_prompt()
    rules_content, _ = get_extraction_rules_prompt()
    
    system_prompt = extract_section(title_prompt, "SYSTEM_PROMPT")
    input_template = extract_section(title_prompt, "INPUT_TEMPLATE")
    rules_message = extract_section(rules_content, "RULES_MESSAGE")
    
    if not system_prompt:
        system_prompt = title_prompt
    
    return system_prompt, input_template, rules_message, title_version


def get_consolidation_prompt_parts() -> tuple[str, str, str, str]:
    """Load and parse consolidation prompt into sections.
    
    The consolidation step compares explicit drug classes (from Step 4) with
    drug-specific selections (from Step 3) and removes duplicates/parents.
    
    Returns:
        Tuple of (system_prompt, input_template, rules_message, version)
    """
    consolidation_prompt, version = get_consolidation_prompt()
    rules_content, _ = get_extraction_rules_prompt()
    
    system_prompt = extract_section(consolidation_prompt, "SYSTEM_PROMPT")
    input_template = extract_section(consolidation_prompt, "INPUT_TEMPLATE")
    rules_message = extract_section(rules_content, "RULES_MESSAGE")
    
    if not system_prompt:
        system_prompt = consolidation_prompt
    
    return system_prompt, input_template, rules_message, version


def get_validation_prompt_parts() -> tuple[str, str, str]:
    """Load validation prompt and extraction rules for reference.
    
    The validation step validates drug class extractions against the 
    extraction rules. It needs the validation prompt and the extraction
    rules (as reference for the validator).
    
    Returns:
        Tuple of (validation_prompt, extraction_rules, version)
    """
    validation_prompt, version = get_validation_prompt()
    extraction_rules, _ = get_extraction_rules_prompt()
    
    return validation_prompt, extraction_rules, version


def clear_prompt_cache() -> None:
    """Clear the prompt cache. Useful for testing or after prompt updates."""
    _prompt_cache.clear()
