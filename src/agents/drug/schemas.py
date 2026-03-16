"""Schemas for drug extraction.

This module defines:
- Dataclasses for function input (Temporal-serializable)
- Pydantic models for LLM structured output

Required fields (no defaults) ensure LLM response matches expected structure.
If LLM returns malformed JSON, Pydantic will raise ValidationError,
which triggers Temporal retry at the activity level.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.agents.core.schemas import BaseActivityInput


# =============================================================================
# Dataclass for Function Input (Temporal-ready)
# =============================================================================

@dataclass
class DrugInput(BaseActivityInput):
    """Input for drug extraction.

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    pass


@dataclass
class ValidationInput(BaseActivityInput):
    """Input for drug validation.

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    extraction_result: dict  # The ExtractionResult as dict


# =============================================================================
# Pydantic Models for Extraction LLM Structured Output
# =============================================================================

class ExtractionResult(BaseModel):
    """Schema for drug extraction LLM response.
    
    Matches DRUG_EXTRACTION_SYSTEM_PROMPT output format.
    
    Required fields (no defaults) ensure LLM response matches expected structure.
    If LLM returns malformed JSON, Pydantic will raise ValidationError.
    """
    # Required field - must be present in LLM response
    reasoning: list[str] = Field(
        ...,  # Required - no default
        alias="Reasoning",
        description="Step-by-step extraction reasoning"
    )
    
    # Optional fields - can be empty/missing
    primary_drugs: list[str] = Field(
        default_factory=list,
        alias="Primary Drugs",
        description="Primary/investigational drugs being studied"
    )
    secondary_drugs: list[str] = Field(
        default_factory=list,
        alias="Secondary Drugs",
        description="Secondary/combination drugs"
    )
    comparator_drugs: list[str] = Field(
        default_factory=list,
        alias="Comparator Drugs",
        description="Comparator/control drugs"
    )
    
    model_config = {"populate_by_name": True}


# =============================================================================
# Pydantic Models for Validation LLM Structured Output
# =============================================================================

class SearchResult(BaseModel):
    """Search result from grounded search."""
    drug_queried: str = Field(default="", description="Drug name that was searched")
    is_therapeutic_drug: bool = Field(default=False, description="Whether it's a valid therapeutic drug")
    source_url: str = Field(default="", description="Authoritative source URL")
    source_title: str = Field(default="", description="Title of the source")
    evidence: str = Field(default="", description="Exact text from source")
    confidence: Literal["high", "medium", "low"] = Field(default="medium", description="Confidence level")


class IssueFound(BaseModel):
    """Issue found during validation."""
    check_type: Literal["hallucination", "omission", "rule_compliance", "misclassification", "synonym_association"] = Field(
        ..., description="Type of issue found"
    )
    severity: Literal["high", "medium", "low"] = Field(default="medium", description="Issue severity")
    description: str = Field(..., description="Clear description of the issue found")
    evidence: str = Field(default="", description="Specific evidence supporting this finding")
    drug: str = Field(default="", description="The specific drug involved")
    correct_category: str = Field(default="", description="Correct category for misclassification")
    rule_reference: str = Field(default="", description="Reference to the rule violated")


class CheckResult(BaseModel):
    """Result of a single validation check."""
    passed: bool = Field(..., description="Whether the check passed")
    note: str = Field(default="", description="Explanation note")


class ChecksPerformed(BaseModel):
    """All validation checks performed."""
    hallucination_detection: CheckResult = Field(..., description="Hallucination check result")
    omission_detection: CheckResult = Field(..., description="Omission check result")
    rule_compliance: CheckResult = Field(..., description="Rule compliance check result")
    misclassification_detection: CheckResult = Field(..., description="Misclassification check result")
    synonym_association_detection: CheckResult = Field(..., description="Synonym association check result")


class ValidationResult(BaseModel):
    """Schema for drug validation LLM response.
    
    Matches DRUG_VALIDATION_SYSTEM_PROMPT output format.
    """
    validation_status: Literal["PASS", "REVIEW", "FAIL"] = Field(
        ..., description="Validation status"
    )
    validation_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0"
    )
    missed_drugs: list[str] = Field(
        default_factory=list, description="Drugs that were missed in extraction"
    )
    grounded_search_performed: bool = Field(
        default=False, description="Whether grounded search was performed"
    )
    search_results: list[SearchResult] = Field(
        default_factory=list, description="Results from grounded search"
    )
    issues_found: list[IssueFound] = Field(
        default_factory=list, description="List of issues found"
    )
    checks_performed: ChecksPerformed = Field(
        ..., description="Results of all validation checks"
    )
    validation_reasoning: str = Field(
        ..., description="Step-by-step validation reasoning"
    )

