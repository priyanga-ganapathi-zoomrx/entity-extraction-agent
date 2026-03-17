"""Unit tests for drug validation agent."""
import pytest
from unittest.mock import MagicMock, patch
from src.agents.drug.validation_agent import validate_drugs, DrugValidationError
from src.agents.drug.schemas import ValidationInput, ValidationResult


@pytest.fixture
def sample_validation_input():
    """Sample validation input."""
    return ValidationInput(
        abstract_id=123,
        abstract_title="Study of Pembrolizumab in NSCLC patients",
        congress_id=1,
        batch_id=100,
        extraction_result={
            "primary_drugs": ["Pembrolizumab"],
            "secondary_drugs": [],
            "comparator_drugs": ["Chemotherapy"],
        },
    )


@pytest.fixture
def validation_input_with_issues():
    """Validation input that should have issues."""
    return ValidationInput(
        abstract_id=456,
        abstract_title="Study of Nivolumab in melanoma",
        congress_id=1,
        batch_id=100,
        extraction_result={
            "primary_drugs": ["Aspirin"],  # Wrong drug (hallucination)
            "secondary_drugs": [],
            "comparator_drugs": [],
        },
    )


@pytest.mark.unit
class TestDrugValidation:
    """Test drug validation agent."""

    def test_validate_drugs_pass(
        self,
        sample_validation_input,
        mock_drug_validation_pass_response,
        mock_llm,
    ):
        """Test successful validation with PASS status."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_drug_validation_pass_response
            mock_create_llm.return_value = mock_llm

            # Execute
            result = validate_drugs(sample_validation_input)

            # Verify
            assert result.validation_status == "PASS"
            assert result.validation_confidence >= 0.9
            assert len(result.issues_found) == 0
            assert result.checks_performed.hallucination_detection.passed is True
            assert result.checks_performed.omission_detection.passed is True
            assert result.checks_performed.rule_compliance.passed is True
            mock_llm.invoke.assert_called_once()

    def test_validate_drugs_review(
        self,
        sample_validation_input,
        mock_drug_validation_review_response,
        mock_llm,
    ):
        """Test validation with REVIEW status."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            mock_llm.invoke.return_value = mock_drug_validation_review_response
            mock_create_llm.return_value = mock_llm

            result = validate_drugs(sample_validation_input)

            assert result.validation_status == "REVIEW"
            assert len(result.issues_found) > 0
            assert len(result.missed_drugs) > 0
            assert result.grounded_search_performed is True
            assert len(result.search_results) > 0

    def test_validate_drugs_fail(
        self,
        validation_input_with_issues,
        mock_drug_validation_fail_response,
        mock_llm,
    ):
        """Test validation with FAIL status."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            mock_llm.invoke.return_value = mock_drug_validation_fail_response
            mock_create_llm.return_value = mock_llm

            result = validate_drugs(validation_input_with_issues)

            assert result.validation_status == "FAIL"
            assert len(result.issues_found) >= 2
            # Check for hallucination issue
            hallucination_issues = [
                i for i in result.issues_found if i.check_type == "hallucination"
            ]
            assert len(hallucination_issues) > 0

    def test_validate_drugs_all_check_types(self, sample_validation_input, mock_llm):
        """Test that all 5 validation checks are performed."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            # Create validation result with all checks
            from src.agents.drug.schemas import ChecksPerformed, CheckResult

            validation_result = ValidationResult(
                validation_status="PASS",
                validation_confidence=1.0,
                checks_performed=ChecksPerformed(
                    hallucination_detection=CheckResult(passed=True, note="Pass"),
                    omission_detection=CheckResult(passed=True, note="Pass"),
                    rule_compliance=CheckResult(passed=True, note="Pass"),
                    misclassification_detection=CheckResult(passed=True, note="Pass"),
                    synonym_association_detection=CheckResult(passed=True, note="Pass"),
                ),
                validation_reasoning="All checks passed",
            )

            mock_llm.invoke.return_value = validation_result
            mock_create_llm.return_value = mock_llm

            result = validate_drugs(sample_validation_input)

            # Verify all 5 checks are present
            assert result.checks_performed.hallucination_detection is not None
            assert result.checks_performed.omission_detection is not None
            assert result.checks_performed.rule_compliance is not None
            assert result.checks_performed.misclassification_detection is not None
            assert result.checks_performed.synonym_association_detection is not None

    def test_validate_drugs_with_misclassification(
        self,
        sample_validation_input,
        mock_llm,
    ):
        """Test validation with misclassification issue."""
        from tests.fixtures.llm_responses import MOCK_DRUG_VALIDATION_WITH_MISCLASSIFICATION

        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            mock_llm.invoke.return_value = MOCK_DRUG_VALIDATION_WITH_MISCLASSIFICATION
            mock_create_llm.return_value = mock_llm

            result = validate_drugs(sample_validation_input)

            assert result.validation_status == "REVIEW"
            misclass_issues = [
                i for i in result.issues_found if i.check_type == "misclassification"
            ]
            assert len(misclass_issues) > 0
            assert misclass_issues[0].correct_category != ""

    def test_validate_drugs_empty_extraction(self, mock_llm):
        """Test validation with empty extraction result."""
        empty_input = ValidationInput(
            abstract_id=789,
            abstract_title="Study with no drugs mentioned",
            congress_id=1,
            batch_id=100,
            extraction_result={
                "primary_drugs": [],
                "secondary_drugs": [],
                "comparator_drugs": [],
            },
        )

        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            from src.agents.drug.schemas import ChecksPerformed, CheckResult

            mock_llm.invoke.return_value = ValidationResult(
                validation_status="PASS",
                validation_confidence=0.95,
                checks_performed=ChecksPerformed(
                    hallucination_detection=CheckResult(passed=True, note="No drugs"),
                    omission_detection=CheckResult(passed=True, note="No drugs to omit"),
                    rule_compliance=CheckResult(passed=True, note="N/A"),
                    misclassification_detection=CheckResult(passed=True, note="N/A"),
                    synonym_association_detection=CheckResult(passed=True, note="N/A"),
                ),
                validation_reasoning="No drugs found, validation not applicable",
            )
            mock_create_llm.return_value = mock_llm

            result = validate_drugs(empty_input)
            assert result.validation_status == "PASS"

    def test_validate_drugs_llm_error(self, sample_validation_input, mock_llm):
        """Test validation when LLM raises an error."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            mock_llm.invoke.side_effect = Exception("LLM API error")
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugValidationError, match="Drug validation failed"):
                validate_drugs(sample_validation_input)

    def test_validate_drugs_with_langfuse_enabled(
        self,
        sample_validation_input,
        mock_drug_validation_pass_response,
        mock_llm,
    ):
        """Test validation with Langfuse enabled."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug.validation_agent.get_client') as mock_get_client, \
             patch('src.agents.drug.validation_agent.CallbackHandler') as mock_callback_handler, \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            # Mock Langfuse client
            mock_lf = MagicMock()
            mock_get_client.return_value = mock_lf
            mock_handler = MagicMock()
            mock_callback_handler.return_value = mock_handler

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_drug_validation_pass_response
            mock_create_llm.return_value = mock_llm

            # Execute
            result = validate_drugs(sample_validation_input)

            # Verify Langfuse was called
            mock_lf.update_current_trace.assert_called_once()
            mock_lf.update_current_generation.assert_called_once()

            # Verify result
            assert result.validation_status == "PASS"

    def test_validate_drugs_with_callbacks(
        self,
        sample_validation_input,
        mock_drug_validation_pass_response,
        mock_llm,
    ):
        """Test validation with custom callbacks."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_drug_validation_pass_response
            mock_create_llm.return_value = mock_llm

            # Create custom callback
            custom_callback = MagicMock()

            # Execute with callback
            result = validate_drugs(sample_validation_input, callbacks=[custom_callback])

            # Verify callback was passed to invoke
            assert result.validation_status == "PASS"
            invoke_call_args = mock_llm.invoke.call_args
            assert "config" in invoke_call_args.kwargs
            assert "callbacks" in invoke_call_args.kwargs["config"]
            assert custom_callback in invoke_call_args.kwargs["config"]["callbacks"]

    def test_validate_drugs_creates_llm_with_correct_config(
        self,
        sample_validation_input,
        mock_drug_validation_pass_response,
        mock_llm,
    ):
        """Test that LLM is created with correct configuration."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")), \
             patch('src.agents.drug.validation_agent.config') as mock_config:

            # Set config values
            mock_config.VALIDATION_MODEL = "gpt-4"
            mock_config.VALIDATION_TEMPERATURE = 0.0
            mock_config.VALIDATION_MAX_TOKENS = 3000
            mock_config.ENABLE_PROMPT_CACHING = False

            mock_llm.invoke.return_value = mock_drug_validation_pass_response
            mock_create_llm.return_value = mock_llm

            validate_drugs(sample_validation_input)

            # Verify create_llm was called with correct config
            mock_create_llm.assert_called_once()
            call_args = mock_create_llm.call_args[0][0]
            assert call_args.model == "gpt-4"
            assert call_args.temperature == 0.0
            assert call_args.max_tokens == 3000
            assert call_args.timeout == 120  # 2 minute timeout

    def test_validate_drugs_three_message_pattern(
        self,
        sample_validation_input,
        mock_drug_validation_pass_response,
        mock_llm,
    ):
        """Test that validation uses 3-message pattern (system, rules, user)."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            mock_llm.invoke.return_value = mock_drug_validation_pass_response
            mock_create_llm.return_value = mock_llm

            validate_drugs(sample_validation_input)

            # Verify 3 messages were sent
            messages = mock_llm.invoke.call_args[0][0]
            assert len(messages) == 3

    def test_validate_drugs_grounded_search_results(
        self,
        sample_validation_input,
        mock_drug_validation_review_response,
        mock_llm,
    ):
        """Test validation with grounded search results."""
        with patch('src.agents.drug.validation_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.validation_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.validation_agent.get_validation_prompt_parts',
                   return_value=("Instructions", "Rules", "v1.0")):

            mock_llm.invoke.return_value = mock_drug_validation_review_response
            mock_create_llm.return_value = mock_llm

            result = validate_drugs(sample_validation_input)

            # Verify grounded search was performed
            assert result.grounded_search_performed is True
            assert len(result.search_results) > 0

            # Verify search result structure
            search_result = result.search_results[0]
            assert search_result.drug_queried != ""
            assert search_result.is_therapeutic_drug is not None
            assert search_result.source_url != ""
            assert search_result.confidence in ["high", "medium", "low"]
