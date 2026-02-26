"""Validation schemas for drug class extraction.

Contains schemas specific to the validation step.
"""

from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# VALIDATION ISSUE
# =============================================================================

class ValidationIssue(BaseModel):
    """A single validation issue found during checks.
    
    Matches the nested structure in LLM response.
    """
    check_type: str = Field(
        ..., 
        description="Type of issue: hallucination | omission | rule_compliance"
    )
    severity: str = Field(
        ..., 
        description="Issue severity: high | medium | low"
    )
    description: str = Field(
        default="", 
        description="Clear description of the issue found"
    )
    evidence: str = Field(
        default="", 
        description="Specific evidence from sources supporting this finding"
    )
    drug_class: str = Field(
        default="", 
        description="The specific drug class involved (if applicable)"
    )
    transformed_drug_class: Optional[str] = Field(
        default=None, 
        description="Correctly transformed drug class (REQUIRED for rule_compliance only)"
    )
    rule_reference: str = Field(
        default="", 
        description="Rule that was violated or should have been applied"
    )


# =============================================================================
# CHECK RESULTS
# =============================================================================

class CheckResult(BaseModel):
    """Result of a single validation check.
    
    Used in checks_performed nested structure.
    """
    passed: bool = Field(default=True, description="Whether the check passed")
    note: str = Field(default="", description="Brief note about the check result")


class ChecksPerformed(BaseModel):
    """Results of all five validation checks.
    
    Nested structure in LLM response.
    """
    hallucination_detection: CheckResult = Field(
        default_factory=CheckResult,
        description="Result of hallucination detection check"
    )
    omission_detection: CheckResult = Field(
        default_factory=CheckResult,
        description="Result of omission detection check"
    )
    rule_compliance: CheckResult = Field(
        default_factory=CheckResult,
        description="Result of rule compliance check"
    )
    title_extraction_compliance: CheckResult = Field(
        default_factory=CheckResult,
        description="Result of title extraction and consolidation rules check"
    )
    selection_rule_compliance: CheckResult = Field(
        default_factory=CheckResult,
        description="Result of prioritization and specificity selection rules check"
    )


# =============================================================================
# VALIDATION LLM RESPONSE
# =============================================================================

class ValidationLLMResponse(BaseModel):
    """LLM structured output for drug class validation.
    
    Matches the response format specified in DRUG_CLASS_VALIDATION_SYSTEM_PROMPT.
    """
    validation_status: str = Field(
        default="REVIEW", 
        description="Overall status: PASS | REVIEW | FAIL"
    )
    validation_confidence: float = Field(
        default=0.0, 
        description="Confidence score 0.0 to 1.0"
    )
    missed_drug_classes: list[str] = Field(
        default_factory=list, 
        description="Drug classes that should have been extracted but were missed"
    )
    issues_found: list[ValidationIssue] = Field(
        default_factory=list, 
        description="List of validation issues found"
    )
    checks_performed: ChecksPerformed = Field(
        default_factory=ChecksPerformed,
        description="Results of the five validation checks"
    )
    validation_reasoning: str = Field(
        default="", 
        description="Step-by-step validation reasoning"
    )


# =============================================================================
# VALIDATION OUTPUT
# =============================================================================

class ValidationOutput(BaseModel):
    """Pipeline-friendly validation output.
    
    Extends LLM response with metadata for tracking.
    """
    # LLM response fields
    validation_status: str = Field(default="REVIEW")
    validation_confidence: float = Field(default=0.0)
    missed_drug_classes: list[str] = Field(default_factory=list)
    issues_found: list[ValidationIssue] = Field(default_factory=list)
    checks_performed: ChecksPerformed = Field(default_factory=ChecksPerformed)
    validation_reasoning: str = Field(default="")
    
    # Metadata
    llm_calls: int = Field(default=0, description="Number of LLM calls made")
    validation_success: bool = Field(default=True, description="Whether validation completed without errors")
    raw_llm_response: Optional[str] = Field(default=None, description="Raw LLM response for debugging")
    
    @classmethod
    def from_llm_response(
        cls, 
        response: ValidationLLMResponse, 
        llm_calls: int = 1,
        raw_response: Optional[str] = None
    ) -> "ValidationOutput":
        """Create ValidationOutput from LLM response."""
        return cls(
            validation_status=response.validation_status,
            validation_confidence=response.validation_confidence,
            missed_drug_classes=response.missed_drug_classes,
            issues_found=response.issues_found,
            checks_performed=response.checks_performed,
            validation_reasoning=response.validation_reasoning,
            llm_calls=llm_calls,
            validation_success=True,
            raw_llm_response=raw_response,
        )
    
    @classmethod
    def error_response(cls, error: str, llm_calls: int = 0) -> "ValidationOutput":
        """Create error ValidationOutput."""
        return cls(
            validation_status="REVIEW",
            validation_confidence=0.0,
            issues_found=[
                ValidationIssue(
                    check_type="system_error",
                    severity="high",
                    description=f"Validation failed: {error}",
                )
            ],
            validation_reasoning=f"Validation could not be completed: {error}",
            llm_calls=llm_calls,
            validation_success=False,
        )

