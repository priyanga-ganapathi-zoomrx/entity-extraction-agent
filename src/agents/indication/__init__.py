"""Indication extraction agents and activities."""

# Extraction agent
from src.agents.indication.extraction_agent import IndicationAgent

# Schemas for activity input and LLM response parsing
from src.agents.indication.schemas import (
    IndicationInput,
    # Extraction schemas
    LLMResponse,
    ExtractionLLMResponse,
    RuleRetrieved,
    ComponentIdentified,
    # Validation schemas
    ValidationLLMResponse,
    IssueFound,
    CheckPerformed,
    ChecksPerformed,
)

# Config
from src.agents.indication.config import config

# Tools
from src.agents.indication.tools import get_tools

# Prompts
from src.agents.indication.prompts import (
    get_extraction_prompt,
    get_validation_prompt,
    EXTRACTION_PROMPT_NAME,
    VALIDATION_PROMPT_NAME,
)

# Validation agent
from src.agents.indication.validation_agent import IndicationValidationAgent

__all__ = [
    # Agents
    "IndicationAgent",
    "IndicationValidationAgent",
    # Input
    "IndicationInput",
    # Extraction schemas
    "LLMResponse",
    "ExtractionLLMResponse",
    "RuleRetrieved",
    "ComponentIdentified",
    # Validation schemas
    "ValidationLLMResponse",
    "IssueFound",
    "CheckPerformed",
    "ChecksPerformed",
    # Config
    "config",
    # Tools & Prompts
    "get_tools",
    "get_extraction_prompt",
    "get_validation_prompt",
    "EXTRACTION_PROMPT_NAME",
    "VALIDATION_PROMPT_NAME",
]
