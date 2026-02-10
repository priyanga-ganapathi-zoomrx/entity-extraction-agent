"""Temporal activities for drug class extraction pipeline.

This module provides activities for the 5-step drug class extraction pipeline:
- Step 1: Regimen identification (is this drug a regimen?)
- Step 2: Drug class extraction (search + LLM extraction)
- Step 3: Drug class selection (pick best class for multi-class drugs)
- Step 4: Explicit extraction (extract classes mentioned in title)
- Step 5: Consolidation (merge and deduplicate)

Activities are thin wrappers around existing agent functions.
They:
- Accept the same input types (dataclasses) as the underlying functions
- Call the existing agent functions
- Serialize outputs to dicts for Temporal serialization
- Let Temporal handle retries (configured in workflow execution)

Error Handling:
- Agent functions use LangChain's with_structured_output() for reliable JSON parsing
- LLM response schemas have required fields (no defaults) to catch malformed responses
- If LLM returns wrong format, Pydantic raises ValidationError
- ValidationError propagates as DrugClassExtractionError, triggering Temporal retry
- Per-request timeout is 120s; Temporal handles retry scheduling

Best Practices Applied:
- Activities are synchronous because underlying LLM calls use synchronous LangChain
- Activities are idempotent - same input produces same output
- Fine-grained activities per step for better observability and retry granularity
- No application-level retry (tenacity removed) - Temporal handles all retries
"""

from temporalio import activity

from src.agents.drug_class.schemas import (
    RegimenInput,
    DrugClassExtractionInput,
    SelectionInput,
    ExplicitExtractionInput,
    ConsolidationInput,
    ValidationInput as DrugClassValidationInput,
)
from src.temporal.idle_shutdown import track_activity


# =============================================================================
# STEP 1: REGIMEN IDENTIFICATION
# =============================================================================

@activity.defn(name="step1_regimen")
@track_activity
def step1_regimen(input_data: RegimenInput) -> dict:
    """Identify if a drug is a regimen and extract its components.
    
    A regimen is a combination therapy with multiple drugs (e.g., "R-CHOP").
    This activity identifies component drugs from regimen names.
    
    Args:
        input_data: RegimenInput dataclass containing:
            - abstract_id: Unique identifier for the abstract
            - abstract_title: The title text
            - drug: Drug name to analyze
    
    Returns:
        list[str]: Component drugs. If not a regimen, returns [drug].
    
    Raises:
        DrugClassExtractionError: If LLM call fails (triggers Temporal retry)
    
    Example:
        >>> input_data = RegimenInput(
        ...     abstract_id="12345",
        ...     abstract_title="Phase 3 study of R-CHOP in DLBCL",
        ...     drug="R-CHOP"
        ... )
        >>> result = step1_regimen(input_data)
        >>> result
        ["rituximab", "cyclophosphamide", "doxorubicin", "vincristine", "prednisone"]
    """
    from src.agents.drug_class.step1_regimen import identify_regimen
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Step 1 - Regimen identification for drug '{input_data.drug}' "
        f"in abstract {input_data.abstract_id}"
    )
    
    # Create tracker and call agent with it
    tracker = TokenUsageCallbackHandler()
    result = identify_regimen(input_data, callbacks=[tracker])
    
    # Step 1 returns list[str] directly - wrap with token metadata
    # Workflow will handle extracting _token_usage before use
    return {
        "_result": result,
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }


# =============================================================================
# STEP 2: DRUG CLASS EXTRACTION
# =============================================================================

@activity.defn(name="step2_fetch_search_results")
@track_activity
def step2_fetch_search_results(
    drug: str,
    firms: list[str],
    storage_base_path: str = "",
) -> dict:
    """Fetch search results for a drug using Tavily API with caching.
    
    This activity fetches drug class and firm search results.
    Results are cached globally to avoid duplicate API calls.
    
    Args:
        drug: Drug name to search
        firms: List of pharmaceutical company names
        storage_base_path: Base path for cache storage
    
    Returns:
        dict: Contains:
            - drug_class_results: List of search results for drug class
            - firm_search_results: List of search results for drug + firm
    
    Example:
        >>> result = step2_fetch_search_results("pembrolizumab", ["Merck"])
        >>> len(result["drug_class_results"])
        5
    """
    from src.agents.drug_class.step2_search import fetch_search_results
    from src.agents.core.storage import get_storage_client
    
    activity.logger.info(
        f"Step 2 - Fetching search results for drug '{drug}'"
    )
    
    # Create storage client for caching (auto-detects GCS vs local)
    storage = get_storage_client(storage_base_path) if storage_base_path else get_storage_client("output")
    
    # Call existing function - returns tuple
    drug_class_results, firm_search_results = fetch_search_results(drug, firms, storage)
    
    return {
        "drug_class_results": drug_class_results,
        "firm_search_results": firm_search_results,
    }


@activity.defn(name="step2_extract_with_tavily")
@track_activity
def step2_extract_with_tavily(input_data: DrugClassExtractionInput) -> dict:
    """Extract drug classes using pre-fetched Tavily search results.
    
    Primary extraction method. Uses LLM to analyze search results and
    extract drug class information.
    
    Args:
        input_data: DrugClassExtractionInput dataclass containing:
            - abstract_id: Unique identifier
            - abstract_title: Title text
            - drug: Drug name to classify
            - full_abstract: Full abstract text (optional)
            - firms: List of firm names
            - drug_class_results: Pre-fetched Tavily drug class search results
            - firm_search_results: Pre-fetched Tavily firm search results
    
    Returns:
        dict: Serialized DrugExtractionResult containing:
            - drug_name: The drug being classified
            - drug_classes: List of extracted drug classes
            - selected_sources: Sources where classes were found
            - confidence_score: Confidence score 0.0-1.0
            - extraction_details: Detailed extraction info
            - extraction_method: "tavily"
            - reasoning: Extraction reasoning
            - success: Whether extraction succeeded
    
    Raises:
        DrugClassExtractionError: If extraction fails (triggers Temporal retry)
    """
    from src.agents.drug_class.step2_extraction import extract_with_tavily
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Step 2 - Tavily extraction for drug '{input_data.drug}' "
        f"in abstract {input_data.abstract_id}"
    )
    
    tracker = TokenUsageCallbackHandler()
    result = extract_with_tavily(input_data, callbacks=[tracker])
    
    return {
        **result.model_dump(),
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }


@activity.defn(name="step2_extract_with_grounded")
@track_activity
def step2_extract_with_grounded(input_data: DrugClassExtractionInput) -> dict:
    """Extract drug classes using LLM's grounded search (web_search_preview).
    
    Fallback extraction method when Tavily returns no results or NA.
    Uses the LLM's built-in web search capability.
    
    Args:
        input_data: DrugClassExtractionInput dataclass containing:
            - abstract_id: Unique identifier
            - abstract_title: Title text
            - drug: Drug name to classify
            - full_abstract: Full abstract text (optional)
    
    Returns:
        dict: Serialized DrugExtractionResult (same format as Tavily)
    
    Raises:
        DrugClassExtractionError: If extraction fails (triggers Temporal retry)
    """
    from src.agents.drug_class.step2_extraction import extract_with_grounded
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Step 2 - Grounded extraction (fallback) for drug '{input_data.drug}' "
        f"in abstract {input_data.abstract_id}"
    )
    
    tracker = TokenUsageCallbackHandler()
    result = extract_with_grounded(input_data, callbacks=[tracker])
    
    return {
        **result.model_dump(),
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }


# =============================================================================
# STEP 3: DRUG CLASS SELECTION
# =============================================================================

@activity.defn(name="step3_selection")
@track_activity
def step3_selection(input_data: SelectionInput) -> dict:
    """Select the best drug class(es) for a drug with multiple classes.
    
    When a drug is associated with multiple drug classes, this activity
    selects the most appropriate one(s) based on prioritization rules:
    MoA > Chemical > Mode > Therapeutic (unless multiple biological targets).
    
    Args:
        input_data: SelectionInput dataclass containing:
            - abstract_id: Unique identifier
            - drug_name: Drug being classified
            - extraction_details: List of extraction detail dicts from Step 2
    
    Returns:
        dict: Serialized DrugSelectionResult containing:
            - drug_name: The drug
            - selected_drug_classes: Selected class(es)
            - reasoning: Selection reasoning
    
    Raises:
        DrugClassExtractionError: If selection fails (triggers Temporal retry)
    
    Note:
        If only one unique class exists, no LLM call is made (optimization).
    """
    from src.agents.drug_class.step3_selection import select_drug_class
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Step 3 - Class selection for drug '{input_data.drug_name}' "
        f"in abstract {input_data.abstract_id}"
    )
    
    tracker = TokenUsageCallbackHandler()
    result = select_drug_class(input_data, callbacks=[tracker])
    
    return {
        **result.model_dump(),
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }


# =============================================================================
# STEP 4: EXPLICIT EXTRACTION
# =============================================================================

@activity.defn(name="step4_explicit")
@track_activity
def step4_explicit(input_data: ExplicitExtractionInput) -> dict:
    """Extract drug classes explicitly mentioned in the abstract title.
    
    This extracts drug classes that are directly mentioned in the title
    (e.g., "PD-1 inhibitor" in the title), not inferred from drug names.
    
    Args:
        input_data: ExplicitExtractionInput dataclass containing:
            - abstract_id: Unique identifier
            - abstract_title: Title text to extract from
    
    Returns:
        dict: Serialized Step4Output containing:
            - explicit_drug_classes: List of explicitly mentioned classes
            - extraction_details: Detailed extraction info
            - reasoning: Extraction reasoning
    
    Raises:
        DrugClassExtractionError: If extraction fails (triggers Temporal retry)
    
    Note:
        Returns ["NA"] if title is empty.
    """
    from src.agents.drug_class.step4_explicit import extract_explicit_classes
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Step 4 - Explicit extraction for abstract {input_data.abstract_id}"
    )
    
    tracker = TokenUsageCallbackHandler()
    result = extract_explicit_classes(input_data, callbacks=[tracker])
    
    return {
        **result.model_dump(),
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }


# =============================================================================
# STEP 5: CONSOLIDATION
# =============================================================================

@activity.defn(name="step5_consolidation")
@track_activity
def step5_consolidation(input_data: ConsolidationInput) -> dict:
    """Consolidate explicit classes with drug-derived classes.
    
    Compares explicit drug classes (from Step 4) with drug-specific
    selections (from Step 3) and removes duplicates and parent classes
    within the same hierarchy.
    
    Args:
        input_data: ConsolidationInput dataclass containing:
            - abstract_id: Unique identifier
            - abstract_title: Title text
            - explicit_drug_classes: Classes from Step 4
            - drug_selections: List of {drug_name, selected_classes} from Step 3
    
    Returns:
        dict: Serialized Step5Output containing:
            - refined_explicit_classes: Classes after deduplication
            - removed_classes: Classes that were removed
            - reasoning: Consolidation reasoning
    
    Raises:
        DrugClassExtractionError: If consolidation fails (triggers Temporal retry)
    
    Note:
        Returns input classes unchanged if no drug selections to compare.
    """
    from src.agents.drug_class.step5_consolidation import consolidate_drug_classes
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Step 5 - Consolidation for abstract {input_data.abstract_id}"
    )
    
    tracker = TokenUsageCallbackHandler()
    result = consolidate_drug_classes(input_data, callbacks=[tracker])
    
    return {
        **result.model_dump(),
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }


# =============================================================================
# STEP 6: VALIDATION
# =============================================================================

@activity.defn(name="validate_drug_class")
@track_activity
def validate_drug_class_activity(input_data: DrugClassValidationInput) -> dict:
    """Validate a drug class extraction result.
    
    Performs three validation checks:
    1. Hallucination Detection - are extracted classes grounded in sources?
    2. Omission Detection - are there valid classes that weren't extracted?
    3. Rule Compliance - were extraction rules applied correctly?
    
    Args:
        input_data: ValidationInput dataclass containing:
            - abstract_id: Unique identifier
            - drug_name: Drug being validated
            - abstract_title: Title text
            - full_abstract: Full abstract text
            - search_results: List of search result dicts
            - extraction_result: Dict of extraction result to validate
    
    Returns:
        dict: Serialized ValidationOutput containing:
            - validation_status: PASS, REVIEW, or FAIL
            - validation_confidence: Confidence score 0.0-1.0
            - issues_found: List of validation issues
            - checks_performed: Results of all checks
            - validation_reasoning: Step-by-step reasoning
    
    Raises:
        DrugClassExtractionError: If validation fails (triggers Temporal retry)
    """
    from src.agents.drug_class.validation import validate_drug_class
    from src.agents.core.token_tracking import TokenUsageCallbackHandler
    
    activity.logger.info(
        f"Validating drug class for drug '{input_data.drug_name}' "
        f"in abstract {input_data.abstract_id}"
    )
    
    tracker = TokenUsageCallbackHandler()
    result = validate_drug_class(input_data, callbacks=[tracker])
    
    return {
        **result.model_dump(),
        "_token_usage": tracker.usage.to_dict(),
        "_llm_calls": tracker.llm_calls,
    }
