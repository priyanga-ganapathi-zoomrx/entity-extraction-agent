"""Output schemas for drug class extraction pipeline steps.

Contains Pydantic models for step outputs and result schemas.
Supports per-drug checkpointing for incremental processing.
"""

from typing import Literal

from pydantic import BaseModel, Field

from src.agents.drug_class.schemas.base import DrugStatus
from src.agents.drug_class.schemas.llm_responses import ExtractionDetail


# =============================================================================
# STEP 1: REGIMEN IDENTIFICATION OUTPUT
# =============================================================================

class Step1Output(BaseModel):
    """Checkpoint output for Step 1 with per-drug tracking.
    
    Supports incremental checkpointing - on retry, only pending/failed drugs
    are reprocessed.
    """
    # Per-drug status tracking
    drug_status: dict[str, DrugStatus] = Field(
        default_factory=dict,
        description="Status of each drug: pending, success, or failed"
    )
    
    # Mapping from drug to components (only for succeeded drugs)
    drug_to_components: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Maps drug name to its components (populated on success)"
    )
    
    # Error messages for failed drugs
    drug_errors: dict[str, str] = Field(
        default_factory=dict,
        description="Error messages for failed drugs"
    )
    
    def is_drug_done(self, drug: str) -> bool:
        """Check if a drug has already been successfully processed."""
        return self.drug_status.get(drug) == "success"
    
    def get_pending_drugs(self, all_drugs: list[str]) -> list[str]:
        """Get list of drugs that need processing (pending or failed)."""
        return [
            drug for drug in all_drugs
            if self.drug_status.get(drug) != "success"
        ]
    
    def get_all_components(self) -> list[str]:
        """Derive unique components from drug_to_components.
        
        Returns:
            List of unique component drugs in order of first appearance.
        """
        seen = set()
        result = []
        for components in self.drug_to_components.values():
            for comp in components:
                if comp not in seen:
                    seen.add(comp)
                    result.append(comp)
        return result
    
    def mark_success(self, drug: str, components: list[str]) -> None:
        """Mark a drug as successfully processed."""
        self.drug_status[drug] = "success"
        self.drug_to_components[drug] = components
        # Clear any previous error
        self.drug_errors.pop(drug, None)
    
    def mark_failed(self, drug: str, error: str) -> None:
        """Mark a drug as failed with error message."""
        self.drug_status[drug] = "failed"
        self.drug_errors[drug] = error
        # Set empty components for failed drug
        self.drug_to_components[drug] = []
    
    def is_complete(self, all_drugs: list[str]) -> bool:
        """Check if all drugs have been successfully processed."""
        return all(
            self.drug_status.get(drug) == "success"
            for drug in all_drugs
        )


# =============================================================================
# STEP 2: DRUG CLASS EXTRACTION OUTPUT
# =============================================================================

class DrugExtractionResult(BaseModel):
    """Extraction result for a single drug."""
    drug_name: str = Field(..., description="Drug being classified")
    drug_classes: list[str] = Field(default_factory=list, description="Extracted drug classes")
    selected_sources: list[str] = Field(
        default_factory=list,
        description="Sources where classes were found: 'abstract_title' | 'abstract_text' | '<url>'"
    )
    confidence_score: float = Field(default=0.0, description="Confidence score 0.0-1.0")
    extraction_details: list[ExtractionDetail] = Field(default_factory=list)
    extraction_method: Literal["tavily", "grounded"] = Field(
        default="tavily",
        description="Search method used: 'tavily' (default) or 'grounded' (fallback)"
    )
    reasoning: str = Field(default="", description="Extraction reasoning")
    success: bool = Field(default=True)
    
    @property
    def requires_validation(self) -> bool:
        """Flag for automatic validation when grounded search was used."""
        return self.extraction_method == "grounded"


class DrugSearchCache(BaseModel):
    """Cached search results for a drug (per-congress, shared across abstracts).
    
    Stored at: congress/{congress_id}/search_cache/{drug_normalized}.json
    """
    drug: str = Field(..., description="Original drug name")
    fetched_at: str = Field(..., description="ISO timestamp when first fetched")
    drug_class_search: dict = Field(
        default_factory=dict,
        description="Drug class search results: {'fetched_at': ..., 'results': [...]}"
    )
    firm_searches: dict[str, dict] = Field(
        default_factory=dict,
        description="Firm search results keyed by firms_key: {'[\"Firm1\"]': {'results': [...]}}"
    )


class Step2Output(BaseModel):
    """Checkpoint output for Step 2 with per-drug tracking.
    
    Supports incremental checkpointing - on retry, only pending/failed drugs
    are reprocessed.
    """
    # Per-drug status tracking
    drug_status: dict[str, DrugStatus] = Field(
        default_factory=dict,
        description="Status of each drug: pending, success, or failed"
    )
    
    # Extraction results keyed by drug name
    extractions: dict[str, DrugExtractionResult] = Field(
        default_factory=dict,
        description="Maps drug name to extraction result"
    )
    
    def is_drug_done(self, drug: str) -> bool:
        """Check if a drug has already been successfully processed."""
        return self.drug_status.get(drug) == "success"
    
    def get_pending_drugs(self, all_drugs: list[str]) -> list[str]:
        """Get list of drugs that need processing (pending or failed)."""
        return [
            drug for drug in all_drugs
            if self.drug_status.get(drug) != "success"
        ]
    
    def mark_success(self, drug: str, result: DrugExtractionResult) -> None:
        """Mark a drug as successfully processed."""
        self.drug_status[drug] = "success"
        self.extractions[drug] = result
    
    def mark_failed(self, drug: str, error: str) -> None:
        """Mark a drug as failed."""
        self.drug_status[drug] = "failed"
        self.extractions[drug] = DrugExtractionResult(
            drug_name=drug,
            drug_classes=["NA"],
            reasoning=f"Extraction failed: {error}",
            success=False,
        )
    
    def is_complete(self, all_drugs: list[str]) -> bool:
        """Check if all drugs have been successfully processed."""
        return all(
            self.drug_status.get(drug) == "success"
            for drug in all_drugs
        )
    
    def get_results_list(self) -> list[DrugExtractionResult]:
        """Convert extractions dict to list for downstream steps."""
        return list(self.extractions.values())


# =============================================================================
# STEP 3: DRUG CLASS SELECTION OUTPUT
# =============================================================================

class DrugSelectionResult(BaseModel):
    """Selection result for a single drug."""
    drug_name: str = Field(..., description="Drug name")
    selected_drug_classes: list[str] = Field(
        default_factory=list,
        description="Selected class(es) - usually one unless multiple targets"
    )
    reasoning: str = Field(default="", description="Selection reasoning")


class Step3Output(BaseModel):
    """Checkpoint output for Step 3 with per-drug tracking.
    
    Supports incremental checkpointing - on retry, only pending/failed drugs
    are reprocessed.
    """
    # Per-drug status tracking
    drug_status: dict[str, DrugStatus] = Field(
        default_factory=dict,
        description="Status of each drug: pending, success, or failed"
    )
    
    # Selection results keyed by drug name
    selections: dict[str, DrugSelectionResult] = Field(
        default_factory=dict,
        description="Maps drug name to selection result"
    )
    
    def is_drug_done(self, drug: str) -> bool:
        """Check if a drug has already been successfully processed."""
        return self.drug_status.get(drug) == "success"
    
    def get_pending_drugs(self, all_drugs: list[str]) -> list[str]:
        """Get list of drugs that need processing (pending or failed)."""
        return [
            drug for drug in all_drugs
            if self.drug_status.get(drug) != "success"
        ]
    
    def mark_success(self, drug: str, result: DrugSelectionResult) -> None:
        """Mark a drug as successfully processed."""
        self.drug_status[drug] = "success"
        self.selections[drug] = result
    
    def mark_failed(self, drug: str, error: str) -> None:
        """Mark a drug as failed."""
        self.drug_status[drug] = "failed"
        self.selections[drug] = DrugSelectionResult(
            drug_name=drug,
            selected_drug_classes=["NA"],
            reasoning=f"Selection failed: {error}",
        )
    
    def is_complete(self, all_drugs: list[str]) -> bool:
        """Check if all drugs have been successfully processed."""
        return all(
            self.drug_status.get(drug) == "success"
            for drug in all_drugs
        )
    
    def get_results_list(self) -> list[DrugSelectionResult]:
        """Convert selections dict to list for downstream steps."""
        return list(self.selections.values())


# =============================================================================
# STEP 4: EXPLICIT EXTRACTION OUTPUT
# =============================================================================

class Step4Output(BaseModel):
    """Checkpoint output for Step 4."""
    explicit_drug_classes: list[str] = Field(default_factory=list)
    extraction_details: list[ExtractionDetail] = Field(default_factory=list)
    reasoning: str = Field(default="", description="Extraction reasoning")


# =============================================================================
# STEP 5: CONSOLIDATION OUTPUT
# =============================================================================

class Step5Output(BaseModel):
    """Checkpoint output for Step 5."""
    refined_explicit_classes: list[str] = Field(
        default_factory=list,
        description="Explicit classes after removing duplicates/parents"
    )
    removed_classes: list[str] = Field(
        default_factory=list,
        description="Classes that were removed"
    )
    reasoning: str = Field(default="", description="Consolidation reasoning")

