"""Temporal activities for abstract extraction.

Activities are thin wrappers around existing agent functions.
They handle:
- Accepting dataclass/Pydantic inputs
- Calling existing agent functions
- Serializing outputs to dicts for Temporal

Activity Groups:
- RESULT_STORAGE: Save step outputs to GCS for download from admin portal
- EXTRACTION_PROGRESS: Update entity_mapping SQL tables with workflow progress
- DRUG: Drug extraction and validation (fast LLM)
- DRUG_CLASS: 5-step drug class pipeline (search + LLM)
- INDICATION: Indication extraction and validation (mixed LLM speeds)

Note: Retry logic is handled by Temporal, not tenacity.
The underlying agent functions may still have tenacity decorators
for non-Temporal use cases.
"""

# =============================================================================
# RESULT STORAGE ACTIVITIES (GCS uploads for downloadable results)
# =============================================================================

from src.temporal.activities.result_storage import save_step_output

RESULT_STORAGE_ACTIVITIES = [
    save_step_output,
]

# =============================================================================
# EXTRACTION PROGRESS ACTIVITIES (SQL status updates)
# =============================================================================

from src.temporal.activities.extraction_progress import update_extraction_progress

EXTRACTION_PROGRESS_ACTIVITIES = [
    update_extraction_progress,
]

# =============================================================================
# DRUG ACTIVITIES
# =============================================================================

from src.temporal.activities.drug import (
    extract_drugs,
    validate_drugs,
)

DRUG_ACTIVITIES = [
    extract_drugs,
    validate_drugs,
]

# =============================================================================
# DRUG CLASS ACTIVITIES (5-step pipeline)
# =============================================================================

from src.temporal.activities.drug_class import (
    step1_regimen,
    step2_fetch_search_results,
    step2_extract_with_tavily,
    step2_extract_with_grounded,
    step3_selection,
    step4_explicit,
    step5_consolidation,
    validate_drug_class_activity,
)

DRUG_CLASS_ACTIVITIES = [
    step1_regimen,
    step2_fetch_search_results,
    step2_extract_with_tavily,
    step2_extract_with_grounded,
    step3_selection,
    step4_explicit,
    step5_consolidation,
    validate_drug_class_activity,
]

# =============================================================================
# INDICATION ACTIVITIES
# =============================================================================

from src.temporal.activities.indication import (
    extract_indication,
    validate_indication,
)

INDICATION_ACTIVITIES = [
    extract_indication,
    validate_indication,
]

# =============================================================================
# COMBINED LISTS
# =============================================================================

ALL_ACTIVITIES = (
    RESULT_STORAGE_ACTIVITIES +
    EXTRACTION_PROGRESS_ACTIVITIES +
    DRUG_ACTIVITIES +
    DRUG_CLASS_ACTIVITIES +
    INDICATION_ACTIVITIES
)

__all__ = [
    # Result storage activities
    "save_step_output",
    "RESULT_STORAGE_ACTIVITIES",
    # Extraction progress activities
    "update_extraction_progress",
    "EXTRACTION_PROGRESS_ACTIVITIES",
    # Drug activities
    "extract_drugs",
    "validate_drugs",
    "DRUG_ACTIVITIES",
    # Drug class activities
    "step1_regimen",
    "step2_fetch_search_results",
    "step2_extract_with_tavily",
    "step2_extract_with_grounded",
    "step3_selection",
    "step4_explicit",
    "step5_consolidation",
    "validate_drug_class_activity",
    "DRUG_CLASS_ACTIVITIES",
    # Indication activities
    "extract_indication",
    "validate_indication",
    "INDICATION_ACTIVITIES",
    # Combined
    "ALL_ACTIVITIES",
]
