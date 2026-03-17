"""Unit tests for drug class validation."""
import pytest
from unittest.mock import MagicMock, patch

from src.agents.drug_class.validation import (
    validate_drug_class,
    _format_search_results,
    _format_validation_input,
)
from src.agents.drug_class.schemas import (
    ValidationInput,
    ValidationLLMResponse,
    ValidationIssue,
    ChecksPerformed,
    CheckResult,
    DrugClassExtractionError,
)


@pytest.fixture
def sample_validation_input():
    """Sample validation input."""
    return ValidationInput(
        abstract_id=123,
        abstract_title="Study of Pembrolizumab in NSCLC",
        congress_id=1,
        batch_id=2,
        drug_name="Pembrolizumab",
        full_abstract="Full abstract text about Pembrolizumab study...",
        search_results=[
            {
                "url": "https://example.com/pembro",
                "content": "Pembrolizumab is a PD-1 inhibitor...",
            }
        ],
        extraction_result={
            "drug_classes": ["PD-1 Inhibitor"],
            "selected_sources": ["https://example.com/pembro"],
            "confidence_score": 0.9,
            "reasoning": "Extracted from reliable source",
            "extraction_details": [],
        },
        drug_selections=[
            {
                "drug_name": "Pembrolizumab",
                "selected_drug_classes": ["PD-1 Inhibitor"],
                "reasoning": "MoA class selected",
            }
        ],
        explicit_drug_classes={
            "drug_classes": ["Immunotherapy"],
            "reasoning": "From title",
        },
        refined_explicit_drug_classes={
            "drug_classes": ["Immunotherapy"],
            "removed_classes": [],
            "reasoning": "No duplicates found",
        },
    )


@pytest.fixture
def mock_validation_pass():
    """Mock validation response - PASS."""
    return ValidationLLMResponse(
        validation_status="PASS",
        validation_confidence=0.95,
        missed_drug_classes=[],
        issues_found=[],
        checks_performed=ChecksPerformed(
            omission_detection=CheckResult(passed=True, note="No omissions detected"),
            rule_compliance=CheckResult(passed=True, note="All rules followed"),
            title_extraction_compliance=CheckResult(passed=True, note="Title rules followed"),
            selection_rule_compliance=CheckResult(passed=True, note="Selection rules followed"),
        ),
        validation_reasoning="All checks passed. Extraction is accurate.",
    )


@pytest.fixture
def mock_validation_review():
    """Mock validation response - REVIEW."""
    return ValidationLLMResponse(
        validation_status="REVIEW",
        validation_confidence=0.6,
        missed_drug_classes=["Monoclonal Antibody"],
        issues_found=[
            ValidationIssue(
                check_type="omission",
                severity="medium",
                description="Missed parent class: Monoclonal Antibody",
                evidence="Source mentions pembrolizumab is a monoclonal antibody",
                drug_class="Monoclonal Antibody",
                rule_reference="extraction_rule_3",
            )
        ],
        checks_performed=ChecksPerformed(
            omission_detection=CheckResult(passed=False, note="Found 1 omission"),
            rule_compliance=CheckResult(passed=True, note="Rules followed"),
            title_extraction_compliance=CheckResult(passed=True, note="Title rules followed"),
            selection_rule_compliance=CheckResult(passed=True, note="Selection rules followed"),
        ),
        validation_reasoning="Found one potential omission that needs review.",
    )


@pytest.fixture
def mock_validation_fail():
    """Mock validation response - FAIL."""
    return ValidationLLMResponse(
        validation_status="FAIL",
        validation_confidence=0.3,
        missed_drug_classes=["PD-1 Inhibitor"],
        issues_found=[
            ValidationIssue(
                check_type="rule_compliance",
                severity="high",
                description="Rule violation: class not properly transformed",
                evidence="Source says 'PD-1 blocker' but extracted as 'PD1 inhibitor'",
                drug_class="PD1 inhibitor",
                transformed_drug_class="PD-1 Inhibitor",
                rule_reference="normalization_rule_2",
            )
        ],
        checks_performed=ChecksPerformed(
            omission_detection=CheckResult(passed=False, note="Major omissions"),
            rule_compliance=CheckResult(passed=False, note="Rule violations found"),
            title_extraction_compliance=CheckResult(passed=True, note="OK"),
            selection_rule_compliance=CheckResult(passed=False, note="Priority violated"),
        ),
        validation_reasoning="Multiple critical issues found.",
    )


@pytest.mark.unit
class TestValidation:
    """Test drug class validation logic."""

    def test_validate_drug_class_pass(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test successful validation with PASS status."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Extraction rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input)

            # Verify
            assert result.validation_status == "PASS"
            assert result.validation_confidence == 0.95
            assert result.missed_drug_classes == []
            assert len(result.issues_found) == 0
            assert result.validation_success is True
            assert result.llm_calls == 1
            mock_llm.with_structured_output.assert_called_once_with(ValidationLLMResponse)

    def test_validate_drug_class_review(
        self,
        sample_validation_input,
        mock_validation_review,
        mock_settings,
    ):
        """Test validation with REVIEW status and issues."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Extraction rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_review
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input)

            # Verify
            assert result.validation_status == "REVIEW"
            assert result.validation_confidence == 0.6
            assert result.missed_drug_classes == ["Monoclonal Antibody"]
            assert len(result.issues_found) == 1
            assert result.issues_found[0].check_type == "omission"
            assert result.issues_found[0].severity == "medium"
            assert not result.checks_performed.omission_detection.passed
            assert result.checks_performed.rule_compliance.passed

    def test_validate_drug_class_fail(
        self,
        sample_validation_input,
        mock_validation_fail,
        mock_settings,
    ):
        """Test validation with FAIL status."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Extraction rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_fail
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input)

            # Verify
            assert result.validation_status == "FAIL"
            assert result.validation_confidence == 0.3
            assert result.missed_drug_classes == ["PD-1 Inhibitor"]
            assert len(result.issues_found) == 1
            assert result.issues_found[0].check_type == "rule_compliance"
            assert result.issues_found[0].severity == "high"
            assert not result.checks_performed.rule_compliance.passed

    def test_validate_drug_class_model_configuration(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test that validation model configuration is used."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt, \
             patch('src.agents.drug_class.validation.config') as mock_config:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")
            mock_config.VALIDATION_MODEL = "gpt-4-turbo"
            mock_config.VALIDATION_TEMPERATURE = 0.1
            mock_config.VALIDATION_MAX_TOKENS = 2000

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input)

            # Verify model configuration was used
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.model == "gpt-4-turbo"
            assert llm_config.temperature == 0.1
            assert llm_config.max_tokens == 2000
            assert result.validation_status == "PASS"

    def test_validate_drug_class_timeout_configuration(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test that LLM is configured with correct timeout."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            validate_drug_class(sample_validation_input)

            # Verify LLM was created with 120s timeout
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.timeout == 120

    def test_validate_drug_class_llm_error(
        self,
        sample_validation_input,
        mock_settings,
    ):
        """Test validation when LLM raises an error."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.side_effect = Exception("LLM API error")
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugClassExtractionError, match="Drug class validation failed"):
                validate_drug_class(sample_validation_input)

    def test_validate_drug_class_with_callbacks(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test validation with custom callbacks."""
        mock_llm = MagicMock()
        mock_callback = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input, callbacks=[mock_callback])

            # Verify callback was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert invoke_call_args[1]['config']['callbacks'] == [mock_callback]
            assert result.validation_status == "PASS"

    def test_validate_drug_class_with_langfuse(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test validation with Langfuse enabled."""
        mock_llm = MagicMock()
        mock_langfuse_client = MagicMock()
        mock_langfuse_handler = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug_class.validation.get_client', return_value=mock_langfuse_client), \
             patch('src.agents.drug_class.validation.CallbackHandler', return_value=mock_langfuse_handler), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input)

            # Verify Langfuse trace was updated
            mock_langfuse_client.update_current_trace.assert_called_once()
            trace_call_args = mock_langfuse_client.update_current_trace.call_args
            tags = trace_call_args[1]['tags']
            assert f"abstract_id:{sample_validation_input.abstract_id}" in tags
            assert f"drug:{sample_validation_input.drug_name}" in tags

            # Verify Langfuse generation was updated
            mock_langfuse_client.update_current_generation.assert_called_once()

            # Verify Langfuse handler was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert mock_langfuse_handler in invoke_call_args[1]['config']['callbacks']

            assert result.validation_status == "PASS"

    def test_validate_drug_class_prompt_caching_enabled(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test validation with prompt caching enabled."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt, \
             patch('src.agents.drug_class.validation.config') as mock_config:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")
            mock_config.ENABLE_PROMPT_CACHING = True
            mock_config.VALIDATION_MODEL = "gpt-4"
            mock_config.VALIDATION_TEMPERATURE = 0.0
            mock_config.VALIDATION_MAX_TOKENS = 2000

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            validate_drug_class(sample_validation_input)

            # Verify messages were created with cache_control
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # System message (first) should have cache_control
            assert isinstance(messages[0].content, list)
            assert messages[0].content[0]['cache_control'] == {"type": "ephemeral"}

            # Reference rules message (second) should have cache_control
            assert isinstance(messages[1].content, list)
            assert messages[1].content[0]['cache_control'] == {"type": "ephemeral"}

    def test_validate_drug_class_prompt_caching_disabled(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test validation with prompt caching disabled."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt, \
             patch('src.agents.drug_class.validation.config') as mock_config:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")
            mock_config.ENABLE_PROMPT_CACHING = False
            mock_config.VALIDATION_MODEL = "gpt-4"
            mock_config.VALIDATION_TEMPERATURE = 0.0
            mock_config.VALIDATION_MAX_TOKENS = 2000

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            validate_drug_class(sample_validation_input)

            # Verify messages were created as plain text
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # System and reference rules messages should be plain strings
            assert isinstance(messages[0].content, str)
            assert isinstance(messages[1].content, str)

    def test_validate_drug_class_message_structure(
        self,
        sample_validation_input,
        mock_validation_pass,
        mock_settings,
    ):
        """Test that validation creates correct message structure."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Extraction rules text", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_validation_pass
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            validate_drug_class(sample_validation_input)

            # Verify message structure
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # Should have 3 messages: system, reference rules, input
            assert len(messages) == 3

            # Verify reference rules message contains rule document
            rules_msg_content = messages[1].content if isinstance(messages[1].content, str) else messages[1].content[0]['text']
            assert "REFERENCE RULES DOCUMENT" in rules_msg_content
            assert "Extraction rules text" in rules_msg_content

            # Verify input message contains drug info
            input_msg_content = messages[2].content
            assert "Pembrolizumab" in input_msg_content
            assert "PD-1 Inhibitor" in input_msg_content

    def test_format_search_results_empty(self):
        """Test formatting empty search results."""
        result = _format_search_results([])
        assert result == "No search results available."

    def test_format_search_results_basic(self):
        """Test formatting basic search results."""
        search_results = [
            {
                "url": "https://example.com/1",
                "content": "Short content",
            },
            {
                "url": "https://example.com/2",
                "raw_content": "Raw content here",
            },
        ]

        result = _format_search_results(search_results)

        assert "### Search Result 1" in result
        assert "### Search Result 2" in result
        assert "https://example.com/1" in result
        assert "https://example.com/2" in result
        assert "Short content" in result
        assert "Raw content here" in result

    def test_format_search_results_truncation(self):
        """Test that long search results are truncated."""
        long_content = "x" * 6000
        search_results = [
            {
                "url": "https://example.com/long",
                "content": long_content,
            }
        ]

        result = _format_search_results(search_results)

        assert "[truncated]" in result
        # Content should be truncated at 5000 chars (not counting formatting)
        # The content line contains approximately 5000 'x' characters plus "... [truncated]"
        assert "xxxx" in result  # Contains x characters
        assert len(long_content) > 5000  # Original was longer
        # Count x's in the result - should be around 5000 (may have 1 extra from line break)
        assert 4999 <= result.count("x") <= 5001

    def test_format_validation_input_basic(self, sample_validation_input):
        """Test formatting validation input."""
        result = _format_validation_input(sample_validation_input)

        # Should contain key information
        assert "Pembrolizumab" in result
        assert "PD-1 Inhibitor" in result
        assert "Study of Pembrolizumab in NSCLC" in result
        assert "https://example.com/pembro" in result
        assert "Drug Class Selection Result to Validate" in result
        assert "Refined Explicit Drug Classes to Validate" in result

    def test_format_validation_input_na_classes(self):
        """Test formatting validation input with NA classes."""
        input_data = ValidationInput(
            abstract_id=123,
            abstract_title="Study",
            congress_id=1,
            batch_id=2,
            drug_name="Unknown Drug",
            full_abstract="Abstract text",
            search_results=[],
            extraction_result={
                "drug_classes": ["NA"],
                "selected_sources": [],
                "confidence_score": 0.0,
                "reasoning": "No classes found",
            },
        )

        result = _format_validation_input(input_data)

        assert '["NA"] (extractor returned no drug class)' in result

    def test_format_validation_input_empty_classes(self):
        """Test formatting validation input with empty classes."""
        input_data = ValidationInput(
            abstract_id=123,
            abstract_title="Study",
            congress_id=1,
            batch_id=2,
            drug_name="Drug",
            full_abstract="Abstract",
            search_results=[],
            extraction_result={
                "drug_classes": [],
                "selected_sources": [],
            },
        )

        result = _format_validation_input(input_data)

        assert '["NA"] (extractor returned no drug class)' in result

    def test_validate_drug_class_multiple_issues(self, sample_validation_input, mock_settings):
        """Test validation with multiple issues across different checks."""
        mock_llm = MagicMock()

        # Create response with multiple issues
        multiple_issues_response = ValidationLLMResponse(
            validation_status="REVIEW",
            validation_confidence=0.5,
            missed_drug_classes=["Class1", "Class2"],
            issues_found=[
                ValidationIssue(
                    check_type="omission",
                    severity="high",
                    description="Missed Class1",
                    evidence="Evidence 1",
                    drug_class="Class1",
                    rule_reference="rule_1",
                ),
                ValidationIssue(
                    check_type="rule_compliance",
                    severity="medium",
                    description="Rule violation",
                    evidence="Evidence 2",
                    drug_class="Class2",
                    transformed_drug_class="Correct Class2",
                    rule_reference="rule_2",
                ),
                ValidationIssue(
                    check_type="selection_rule",
                    severity="low",
                    description="Selection priority issue",
                    evidence="Evidence 3",
                    drug_class="Class3",
                    rule_reference="rule_3",
                ),
            ],
            checks_performed=ChecksPerformed(
                omission_detection=CheckResult(passed=False, note="2 omissions"),
                rule_compliance=CheckResult(passed=False, note="1 violation"),
                title_extraction_compliance=CheckResult(passed=True, note="OK"),
                selection_rule_compliance=CheckResult(passed=False, note="Priority issue"),
            ),
            validation_reasoning="Multiple issues across different checks.",
        )

        with patch('src.agents.drug_class.validation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.validation.settings', mock_settings), \
             patch('src.agents.drug_class.validation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.validation.get_validation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = multiple_issues_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = validate_drug_class(sample_validation_input)

            # Verify multiple issues were captured
            assert len(result.issues_found) == 3
            assert result.missed_drug_classes == ["Class1", "Class2"]
            assert result.issues_found[0].check_type == "omission"
            assert result.issues_found[1].check_type == "rule_compliance"
            assert result.issues_found[2].check_type == "selection_rule"
            assert result.issues_found[1].transformed_drug_class == "Correct Class2"

    def test_validation_output_from_llm_response(self, mock_validation_pass):
        """Test ValidationOutput.from_llm_response conversion."""
        from src.agents.drug_class.schemas.validation import ValidationOutput

        output = ValidationOutput.from_llm_response(
            mock_validation_pass,
            llm_calls=1,
            raw_response="raw text"
        )

        assert output.validation_status == "PASS"
        assert output.validation_confidence == 0.95
        assert output.llm_calls == 1
        assert output.validation_success is True
        assert output.raw_llm_response == "raw text"

    def test_validation_output_error_response(self):
        """Test ValidationOutput.error_response factory method."""
        from src.agents.drug_class.schemas.validation import ValidationOutput

        output = ValidationOutput.error_response("Test error", llm_calls=2)

        assert output.validation_status == "REVIEW"
        assert output.validation_confidence == 0.0
        assert output.llm_calls == 2
        assert output.validation_success is False
        assert len(output.issues_found) == 1
        assert output.issues_found[0].check_type == "system_error"
        assert output.issues_found[0].severity == "high"
        assert "Test error" in output.issues_found[0].description
