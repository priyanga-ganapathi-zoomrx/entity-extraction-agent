"""Temporal activities for drug extraction and validation.

These activities are thin wrappers around existing agent functions.
They:
- Accept the same input types (dataclasses) as the underlying functions
- Call the existing agent functions
- Serialize Pydantic outputs to dicts for Temporal serialization
- Let Temporal handle retries (configured in workflow execution)
- Publish structured EMS events (success/failure) to Pub/Sub

Best Practices Applied:
- Activities are synchronous because underlying LLM calls use synchronous LangChain
- Activities are idempotent - same input produces same output
- Non-retryable errors (ValueError, ValidationError) configured in retry policy
- Timeouts and retries configured at workflow level, not in activities
"""

import time

from temporalio import activity

from src.agents.core.ems_logger import get_logger
from src.agents.core.token_tracking import TokenUsageCallbackHandler
from src.agents.drug.config import config as drug_config
from src.agents.drug.extraction_agent import extract_drugs as _extract_drugs
from src.agents.drug.schemas import DrugInput, ValidationInput
from src.agents.drug.validation_agent import validate_drugs as _validate_drugs
from src.temporal.idle_shutdown import track_activity


@activity.defn(name="extract_drugs")
@track_activity
def extract_drugs(input_data: DrugInput) -> dict:
    """Extract drugs from an abstract title.
    
    This activity wraps the existing drug extraction agent function.
    
    Args:
        input_data: DrugInput dataclass containing:
            - abstract_id: Unique identifier for the abstract
            - abstract_title: The title text to extract drugs from
    
    Returns:
        dict: Serialized ExtractionResult containing:
            - primary_drugs: List of primary/investigational drugs
            - secondary_drugs: List of secondary/combination drugs
            - comparator_drugs: List of comparator/control drugs
            - reasoning: Step-by-step extraction reasoning
    
    Raises:
        DrugExtractionError: If LLM call fails (will trigger Temporal retry)
    
    Example:
        >>> input_data = DrugInput(
        ...     abstract_id="12345",
        ...     abstract_title="Phase 3 study of pembrolizumab vs placebo in NSCLC"
        ... )
        >>> result = extract_drugs(input_data)
        >>> result["primary_drugs"]
        ["pembrolizumab"]
    """
    activity.logger.info(
        f"Extracting drugs for abstract {input_data.abstract_id}"
    )
    
    ems_logger = get_logger("drug_extraction")
    info = activity.info()
    tracker = TokenUsageCallbackHandler()
    start = time.time()
    
    try:
        result = _extract_drugs(input_data, callbacks=[tracker])
        duration_ms = int((time.time() - start) * 1000)
        result_dict = result.model_dump()
        
        ems_logger.info(
            "step_completed",
            abstract_id=input_data.abstract_id,
            model=drug_config.EXTRACTION_MODEL,
            input_data={
                "abstract_id": input_data.abstract_id,
                "abstract_title": input_data.abstract_title,
            },
            output={
                "primary_drugs": result_dict.get("primary_drugs"),
                "secondary_drugs": result_dict.get("secondary_drugs"),
                "comparator_drugs": result_dict.get("comparator_drugs"),
            },
            outcome="success",
            llm_calls=tracker.llm_calls,
            input_tokens=tracker.usage.input_tokens,
            output_tokens=tracker.usage.output_tokens,
            total_tokens=tracker.usage.total_tokens,
            duration_ms=duration_ms,
            attempt=info.attempt,
            workflow_run_id=info.workflow_run_id,
        )
        
        # Serialize Pydantic model to dict with token metadata for workflow
        return {
            **result_dict,
            "_token_usage": tracker.usage.to_dict(),
            "_llm_calls": tracker.llm_calls,
        }
        
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        ems_logger.error(
            "step_failed",
            abstract_id=input_data.abstract_id,
            model=drug_config.EXTRACTION_MODEL,
            input_data={
                "abstract_id": input_data.abstract_id,
                "abstract_title": input_data.abstract_title,
            },
            error=str(e),
            outcome="failure",
            exc_info=True,
            duration_ms=duration_ms,
            attempt=info.attempt,
            workflow_run_id=info.workflow_run_id,
        )
        raise


@activity.defn(name="validate_drugs")
@track_activity
def validate_drugs(input_data: ValidationInput) -> dict:
    """Validate extracted drugs against rules.
    
    This activity wraps the existing drug validation agent function.
    
    Args:
        input_data: ValidationInput dataclass containing:
            - abstract_id: Unique identifier for the abstract
            - abstract_title: The original title text
            - extraction_result: Dict of extracted drugs to validate
    
    Returns:
        dict: Serialized ValidationResult containing:
            - validation_status: "PASS", "REVIEW", or "FAIL"
            - validation_confidence: Confidence score 0.0-1.0
            - missed_drugs: List of drugs that were missed
            - issues_found: List of validation issues
            - checks_performed: Results of all validation checks
            - validation_reasoning: Step-by-step reasoning
    
    Raises:
        DrugValidationError: If LLM call fails (will trigger Temporal retry)
    
    Example:
        >>> input_data = ValidationInput(
        ...     abstract_id="12345",
        ...     abstract_title="Phase 3 study of pembrolizumab vs placebo in NSCLC",
        ...     extraction_result={"primary_drugs": ["pembrolizumab"], ...}
        ... )
        >>> result = validate_drugs(input_data)
        >>> result["validation_status"]
        "PASS"
    """
    activity.logger.info(
        f"Validating drugs for abstract {input_data.abstract_id}"
    )
    
    ems_logger = get_logger("drug_validation")
    info = activity.info()
    tracker = TokenUsageCallbackHandler()
    start = time.time()
    
    try:
        result = _validate_drugs(input_data, callbacks=[tracker])
        duration_ms = int((time.time() - start) * 1000)
        result_dict = result.model_dump()
        
        ems_logger.info(
            "step_completed",
            abstract_id=input_data.abstract_id,
            model=drug_config.VALIDATION_MODEL,
            input_data={
                "abstract_id": input_data.abstract_id,
                "abstract_title": input_data.abstract_title,
                "primary_drugs": input_data.extraction_result.get("primary_drugs"),
            },
            output={
                "validation_status": result_dict.get("validation_status"),
                "validation_confidence": result_dict.get("validation_confidence"),
            },
            outcome="success",
            llm_calls=tracker.llm_calls,
            input_tokens=tracker.usage.input_tokens,
            output_tokens=tracker.usage.output_tokens,
            total_tokens=tracker.usage.total_tokens,
            duration_ms=duration_ms,
            attempt=info.attempt,
            workflow_run_id=info.workflow_run_id,
        )
        
        # Serialize Pydantic model to dict with token metadata for workflow
        return {
            **result_dict,
            "_token_usage": tracker.usage.to_dict(),
            "_llm_calls": tracker.llm_calls,
        }
        
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        ems_logger.error(
            "step_failed",
            abstract_id=input_data.abstract_id,
            model=drug_config.VALIDATION_MODEL,
            input_data={
                "abstract_id": input_data.abstract_id,
                "abstract_title": input_data.abstract_title,
                "primary_drugs": input_data.extraction_result.get("primary_drugs"),
            },
            error=str(e),
            outcome="failure",
            exc_info=True,
            duration_ms=duration_ms,
            attempt=info.attempt,
            workflow_run_id=info.workflow_run_id,
        )
        raise
