"""Drug Class Validation Step.

Validates drug class extractions against extraction rules.
Performs five checks:
1. Hallucination Detection - Are extracted classes grounded in sources?
2. Omission Detection - Are there valid classes that weren't extracted?
3. Rule Compliance - Were extraction rules applied correctly?
4. Title Extraction Compliance - Were title extraction and consolidation rules followed?
5. Selection Rule Compliance - Were prioritization and specificity rules applied?

This module exports a single function. Uses with_structured_output for reliable parsing.
Includes per-request timeout (120s). Retries are handled by Temporal.
"""

import json

from langfuse import observe, get_client
from langfuse.langchain import CallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.core import settings, create_llm, LLMConfig
from src.agents.core.langfuse_config import is_langfuse_enabled
from src.agents.drug_class.config import config
from src.agents.drug_class.prompts import (
    get_validation_prompt_parts,
    VALIDATION_PROMPT_NAME,
)
from src.agents.drug_class.schemas import (
    ValidationInput,
    ValidationLLMResponse,
    ValidationOutput,
    DrugClassExtractionError,
)


def _format_search_results(search_results: list[dict]) -> str:
    """Format search results for the validation input.
    
    Args:
        search_results: List of search result dictionaries
        
    Returns:
        Formatted search results string
    """
    if not search_results:
        return "No search results available."
    
    formatted_parts = []
    for i, result in enumerate(search_results, 1):
        content = result.get("raw_content") or result.get("content", "No content available")
        url = result.get("url", "Unknown URL")
        
        # Truncate long content
        if len(content) > 5000:
            content = content[:5000] + "... [truncated]"
        
        formatted_parts.append(f"### Search Result {i}")
        formatted_parts.append(f"**URL**: {url}")
        formatted_parts.append(f"**Content**: {content}")
        formatted_parts.append("")
    
    return "\n".join(formatted_parts)


def _format_validation_input(input_data: ValidationInput) -> str:
    """Format the validation input for the LLM.
    
    Args:
        input_data: ValidationInput with extraction data to validate
        
    Returns:
        Formatted input message string
    """
    extraction_result = input_data.extraction_result
    
    # Format drug classes for display
    drug_classes = extraction_result.get("drug_classes", ["NA"])
    if drug_classes == ["NA"] or not drug_classes:
        drug_classes_display = '["NA"] (extractor returned no drug class)'
    else:
        drug_classes_display = json.dumps(drug_classes)
    
    # Format search results
    search_results_str = _format_search_results(input_data.search_results)
    
    # Format extraction details
    extraction_details = extraction_result.get("extraction_details", [])
    extraction_details_str = json.dumps(extraction_details, indent=2, ensure_ascii=False) if extraction_details else "[]"
    
    input_content = f"""## Validation Input

### Drug Information
- **drug_name**: {input_data.drug_name}

### Original Sources

**Abstract Title:**
{input_data.abstract_title or "Not provided"}

**Full Abstract:**
{input_data.full_abstract or "Not provided"}

**Search Results:**
{search_results_str}

### Extraction Result to Validate
- **drug_classes**: {drug_classes_display}
- **selected_sources**: {json.dumps(extraction_result.get("selected_sources", []))}
- **confidence_score**: {extraction_result.get("confidence_score", "N/A")}
- **reasoning**: {extraction_result.get("reasoning", "")}
- **extraction_details**: 
{extraction_details_str}

Please perform all 3 validation checks (Hallucination Detection, Omission Detection, Rule Compliance) and return your validation result in the specified JSON format."""
    
    return input_content


@observe(as_type="generation", name="drug-class-validation")
def validate_drug_class(input_data: ValidationInput, callbacks: list = None) -> ValidationOutput:
    """Validate a drug class extraction result.
    
    Performs five validation checks:
    1. Hallucination Detection - verifies extracted classes are grounded
    2. Omission Detection - checks for missed valid classes
    3. Rule Compliance - verifies extraction rules were applied correctly
    4. Title Extraction Compliance - verifies title extraction and consolidation rules were followed
    5. Selection Rule Compliance - verifies prioritization and specificity rules were applied
    
    Uses with_structured_output for reliable parsing.
    
    Args:
        input_data: ValidationInput with extraction data to validate
        callbacks: Optional list of LangChain callback handlers (e.g., TokenUsageCallbackHandler)
        
    Returns:
        ValidationOutput with validation status and any issues found
    """
    # Load prompts
    validation_prompt, extraction_rules, prompt_version = get_validation_prompt_parts()
    
    # Format the reference rules message
    reference_rules_content = f"""## REFERENCE RULES DOCUMENT

The following is the complete extraction rules document that the extractor was instructed to follow. Use this as your authoritative reference to verify compliance.

---

{extraction_rules}

---

END OF REFERENCE RULES DOCUMENT"""
    
    # Format the validation input message
    input_content = _format_validation_input(input_data)
    
    # Create LLM with structured output (120s timeout for long-running requests)
    base_llm = create_llm(LLMConfig(
        api_key=settings.llm.LLM_API_KEY,
        base_url=settings.llm.LLM_BASE_URL,
        model=config.VALIDATION_MODEL,
        temperature=config.VALIDATION_TEMPERATURE,
        max_tokens=config.VALIDATION_MAX_TOKENS,
        timeout=120,  # 2 minute timeout
    ))
    llm = base_llm.with_structured_output(ValidationLLMResponse)
    
    # Build messages with optional caching
    if config.ENABLE_PROMPT_CACHING:
        system_msg = SystemMessage(content=[{
            "type": "text",
            "text": validation_prompt,
            "cache_control": {"type": "ephemeral"}
        }])
        reference_rules_msg = HumanMessage(content=[{
            "type": "text",
            "text": reference_rules_content,
            "cache_control": {"type": "ephemeral"}
        }])
    else:
        system_msg = SystemMessage(content=validation_prompt)
        reference_rules_msg = HumanMessage(content=reference_rules_content)
    
    input_msg = HumanMessage(content=input_content)
    messages = [system_msg, reference_rules_msg, input_msg]
    
    # Setup Langfuse callback and metadata if enabled
    langfuse_handler = None
    if is_langfuse_enabled():
        lf = get_client()
        lf.update_current_trace(
            tags=[
                f"abstract_id:{input_data.abstract_id}",
                f"drug:{input_data.drug_name}",
                f"prompt_version:{prompt_version}",
                f"model:{config.VALIDATION_MODEL}",
                f"prompt_name:{VALIDATION_PROMPT_NAME}",
            ],
        )
        lf.update_current_generation(
            model=config.VALIDATION_MODEL,
            metadata={
                "abstract_id": input_data.abstract_id,
                "drug": input_data.drug_name,
                "prompt_version": prompt_version,
            },
        )
        langfuse_handler = CallbackHandler()
    
    all_callbacks = []
    if langfuse_handler:
        all_callbacks.append(langfuse_handler)
    if callbacks:
        all_callbacks.extend(callbacks)
    invoke_config = {"callbacks": all_callbacks} if all_callbacks else {}
    
    try:
        result: ValidationLLMResponse = llm.invoke(messages, config=invoke_config)
        
        return ValidationOutput.from_llm_response(result, llm_calls=1)
        
    except Exception as e:
        raise DrugClassExtractionError(
            f"Drug class validation failed for {input_data.drug_name}: {e}"
        ) from e

