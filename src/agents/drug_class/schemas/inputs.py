"""Input schemas for drug class extraction pipeline.

Contains dataclass inputs for each pipeline step.
These are Temporal-serializable and used as function arguments.
"""

from dataclasses import dataclass, field

from src.agents.core.schemas import BaseActivityInput


# =============================================================================
# PIPELINE INPUT (from drug extraction module)
# =============================================================================

@dataclass
class DrugClassInput(BaseActivityInput):
    """Input for the drug class extraction pipeline.

    Chains from drug extraction output.

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    full_abstract: str = ""
    primary_drugs: list[str] = field(default_factory=list)
    firms: list[str] = field(default_factory=list)  # For firm search in Step 2


# =============================================================================
# STEP 1: REGIMEN IDENTIFICATION
# =============================================================================

@dataclass
class RegimenInput(BaseActivityInput):
    """Input for regimen identification (single drug).

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    drug: str = ""  # Required but set to empty string for dataclass compatibility


# =============================================================================
# STEP 2: DRUG CLASS EXTRACTION
# =============================================================================

@dataclass
class DrugClassExtractionInput(BaseActivityInput):
    """Input for drug class extraction (single drug).

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    drug: str = ""  # Required but set to empty string for dataclass compatibility
    full_abstract: str = ""
    firms: list[str] = field(default_factory=list)
    drug_class_results: list[dict] = field(default_factory=list)  # From Tavily drug class search
    firm_search_results: list[dict] = field(default_factory=list)  # From Tavily firm search


# =============================================================================
# STEP 3: DRUG CLASS SELECTION
# =============================================================================

@dataclass
class SelectionInput(BaseActivityInput):
    """Input for drug class selection (single drug).

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id

    Note: drug_name is used instead of abstract_title for this specific step.
    """
    drug_name: str = ""  # Required but set to empty string for dataclass compatibility
    extraction_details: list[dict] = field(default_factory=list)


# =============================================================================
# STEP 4: EXPLICIT EXTRACTION
# =============================================================================

@dataclass
class ExplicitExtractionInput(BaseActivityInput):
    """Input for explicit extraction from title.

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    pass


# =============================================================================
# STEP 5: CONSOLIDATION
# =============================================================================

@dataclass
class ConsolidationInput(BaseActivityInput):
    """Input for consolidation.

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    explicit_drug_classes: list[str] = field(default_factory=list)
    drug_selections: list[dict] = field(default_factory=list)  # [{drug_name, selected_classes}, ...]


# =============================================================================
# VALIDATION
# =============================================================================

@dataclass
class ValidationInput(BaseActivityInput):
    """Input for drug class validation.

    Contains the original extraction inputs and the result to validate,
    plus outputs from steps 3-5 for selection/title/consolidation checks.

    Inherits transaction context from BaseActivityInput:
    - abstract_id, abstract_title, congress_id, batch_id
    """
    drug_name: str = ""  # Required but set to empty string for dataclass compatibility
    full_abstract: str = ""
    search_results: list[dict] = field(default_factory=list)  # [{url, content}, ...]
    extraction_result: dict = field(default_factory=dict)  # {drug_classes, selected_sources, reasoning, extraction_details}
    drug_selections: list[dict] = field(default_factory=list)  # Step 3: [{drug_name, selected_classes, reasoning}, ...]
    explicit_drug_classes: dict = field(default_factory=dict)  # Step 4: {drug_classes, reasoning}
    refined_explicit_drug_classes: dict = field(default_factory=dict)  # Step 5: {drug_classes, removed_classes, reasoning}

