"""Schemas for indication extraction.

This module defines:
- Temporal-serializable dataclasses for activity input/output
- Pydantic models for LLM response parsing (matches prompt output format)
"""

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Dataclass for Activity Input
# =============================================================================

@dataclass
class IndicationInput:
    """Input for indication extraction activity."""
    abstract_id: str
    abstract_title: str
    session_title: str = ""
    # Relative path to rules CSV within GCS_BUCKET_NAME (e.g. "rules/indication/v3.csv")
    rules_file_path: str = ""


# =============================================================================
# Pydantic Models for Extraction LLM Response Parsing
# =============================================================================

class RuleRetrieved(BaseModel):
    """Rule that was retrieved during extraction."""
    category: str = Field(default="", description="Rule category")
    subcategories: list[str] = Field(default_factory=list, description="Subcategories queried")
    reason: str = Field(default="", description="Reason for retrieving this rule")


class ComponentIdentified(BaseModel):
    """Component identified in the title."""
    component: str = Field(default="", description="Original component text")
    type: str = Field(default="", description="Component type (e.g., Gene Mutation, Disease)")
    normalized_form: str = Field(default="", description="Normalized form after rules applied")
    rule_applied: str = Field(default="", description="Rule that was applied")


class ExtractionLLMResponse(BaseModel):
    """Full schema for parsing extraction LLM JSON response.
    
    Matches the output format defined in MEDICAL_INDICATION_EXTRACTION_SYSTEM_PROMPT.
    
    Required fields (no defaults) ensure LLM response matches expected structure.
    If LLM returns malformed JSON, Pydantic will raise ValidationError.
    """
    # Required field - must be present in LLM response
    reasoning: str = Field(
        ...,  # Required - no default
        description="Step-by-step extraction reasoning"
    )
    
    # Optional fields - can be empty/missing
    selected_source: str = Field(
        default="none",
        description="Source used: abstract_title, session_title, or none"
    )
    generated_indication: str = Field(
        default="",
        description="The extracted medical indication (empty if no indication found)"
    )
    rules_retrieved: list[RuleRetrieved] = Field(
        default_factory=list,
        description="Rules that were retrieved during extraction"
    )
    components_identified: list[ComponentIdentified] = Field(
        default_factory=list,
        description="Components identified in the title"
    )


# Alias for backward compatibility
LLMResponse = ExtractionLLMResponse


# =============================================================================
# Pydantic Models for Validation LLM Response Parsing
# =============================================================================

class IssueFound(BaseModel):
    """Issue found during validation."""
    check_type: str = Field(
        default="",
        description="Type: hallucination, omission, source_selection, rule_application, exclusion_compliance, formatting"
    )
    severity: str = Field(default="medium", description="Severity: high, medium, low")
    description: str = Field(default="", description="Clear description of the issue")
    evidence: str = Field(default="", description="Specific evidence supporting this finding")
    component: str = Field(default="", description="The specific component involved")


class CheckPerformed(BaseModel):
    """Result of a single validation check."""
    passed: bool = Field(default=False, description="Whether the check passed")
    note: str = Field(default="", description="Explanation note for the check result")


class ChecksPerformed(BaseModel):
    """All validation checks performed."""
    source_selection: Optional[CheckPerformed] = None
    hallucination_check: Optional[CheckPerformed] = None
    omission_check: Optional[CheckPerformed] = None
    rule_application: Optional[CheckPerformed] = None
    exclusion_compliance: Optional[CheckPerformed] = None
    formatting_compliance: Optional[CheckPerformed] = None
    abbreviation_check: Optional[CheckPerformed] = None


class ValidationLLMResponse(BaseModel):
    """Full schema for parsing validation LLM JSON response.
    
    Matches the output format defined in INDICATION_VALIDATION_SYSTEM_PROMPT.
    
    Required fields (no defaults) ensure LLM response matches expected structure.
    If LLM returns malformed JSON, Pydantic will raise ValidationError.
    """
    # Required fields - must be present in LLM response
    validation_status: str = Field(
        ...,  # Required - no default
        description="Status: PASS, REVIEW, or FAIL"
    )
    validation_reasoning: str = Field(
        ...,  # Required - no default
        description="Step-by-step explanation of validation process"
    )
    
    # Optional fields - can be empty/missing
    issues_found: list[IssueFound] = Field(
        default_factory=list,
        description="List of issues found during validation"
    )
    checks_performed: Optional[ChecksPerformed] = Field(
        default=None,
        description="Results of all 7 validation checks"
    )
