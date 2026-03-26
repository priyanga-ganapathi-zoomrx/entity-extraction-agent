"""Unit tests for temporal indication activities.

Tests the Temporal activity wrappers for indication extraction and validation.
Activities are thin wrappers that:
- Accept IndicationInput dataclass inputs
- Instantiate and invoke LangGraph agent classes
- Parse agent output messages
- Serialize outputs to dicts
- Track tokens and LLM calls
- Publish EMS events
"""
import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage
from temporalio.exceptions import ApplicationError

from src.temporal.activities.indication import (
    extract_indication,
    validate_indication,
    _parse_json_from_message,
    _extract_result_from_messages,
    IndicationExtractionError,
)
from src.agents.indication.schemas import (
    IndicationInput,
    ExtractionLLMResponse,
    ValidationLLMResponse,
    RuleRetrieved,
    ComponentIdentified,
    CheckPerformed,
    ChecksPerformed,
)


@pytest.mark.unit
class TestParseJsonFromMessage:
    """Test JSON parsing from LLM messages."""

    def test_parse_json_from_markdown_block(self):
        """Test parsing JSON from markdown code block."""
        content = '''
Here's the result:
```json
{
    "reasoning": "Extracted from title",
    "selected_source": "abstract_title",
    "generated_indication": "Non-small cell lung cancer"
}
```
'''
        result = _parse_json_from_message(content, ExtractionLLMResponse)

        assert result.selected_source == "abstract_title"
        assert result.generated_indication == "Non-small cell lung cancer"
        assert result.reasoning == "Extracted from title"

    def test_parse_json_from_raw_json(self):
        """Test parsing raw JSON without code blocks."""
        content = '''{"reasoning": "Test", "selected_source": "session_title", "generated_indication": "Melanoma"}'''

        result = _parse_json_from_message(content, ExtractionLLMResponse)

        assert result.selected_source == "session_title"
        assert result.generated_indication == "Melanoma"

    def test_parse_json_takes_last_code_block(self):
        """Test that parser takes the last code block when multiple exist."""
        content = '''
Input was:
```json
{"old": "data"}
```

Output:
```json
{
    "reasoning": "Extracted from title",
    "selected_source": "abstract_title",
    "generated_indication": "Breast cancer"
}
```
'''
        result = _parse_json_from_message(content, ExtractionLLMResponse)

        assert result.selected_source == "abstract_title"
        assert result.generated_indication == "Breast cancer"

    def test_parse_json_no_json_found(self):
        """Test that ValueError is raised when no JSON found."""
        content = "No JSON here, just text"

        with pytest.raises(ValueError) as exc_info:
            _parse_json_from_message(content, ExtractionLLMResponse)

        assert "No JSON found" in str(exc_info.value)

    def test_parse_json_schema_validation_error(self):
        """Test that schema validation errors raise IndicationExtractionError."""
        content = '''{"wrong": "schema"}'''

        with pytest.raises(IndicationExtractionError) as exc_info:
            _parse_json_from_message(content, ExtractionLLMResponse)

        assert "doesn't match" in str(exc_info.value)
        assert "schema" in str(exc_info.value).lower()


@pytest.mark.unit
class TestExtractResultFromMessages:
    """Test result extraction from agent messages."""

    def test_extract_from_last_ai_message(self):
        """Test extracting result from the last AI message."""
        messages = [
            AIMessage(content="Thinking..."),
            AIMessage(content='{"reasoning": "Test", "selected_source": "abstract_title", "generated_indication": "NSCLC"}'),
        ]

        result = _extract_result_from_messages(messages, ExtractionLLMResponse)

        assert result["selected_source"] == "abstract_title"
        assert result["generated_indication"] == "NSCLC"

    def test_extract_skips_invalid_messages(self):
        """Test that extraction skips messages without valid JSON."""
        messages = [
            AIMessage(content="Invalid JSON here"),
            AIMessage(content='{"reasoning": "Test", "selected_source": "session_title", "generated_indication": "Melanoma"}'),
        ]

        result = _extract_result_from_messages(messages, ExtractionLLMResponse)

        assert result["selected_source"] == "session_title"
        assert result["generated_indication"] == "Melanoma"

    def test_extract_no_valid_messages(self):
        """Test that IndicationExtractionError is raised when no valid JSON found."""
        messages = [
            AIMessage(content="No JSON here"),
            AIMessage(content="Still no JSON"),
        ]

        with pytest.raises(IndicationExtractionError) as exc_info:
            _extract_result_from_messages(messages, ExtractionLLMResponse)

        assert "No valid" in str(exc_info.value)

    def test_extract_schema_validation_failure_bubbles_up(self):
        """Test that schema validation errors bubble up immediately."""
        messages = [
            AIMessage(content='{"wrong": "schema"}'),
            AIMessage(content='{"selected_source": "abstract_title", "generated_indication": "Valid"}'),
        ]

        # Schema validation failure should bubble up immediately, not try next message
        with pytest.raises(IndicationExtractionError):
            _extract_result_from_messages(messages, ExtractionLLMResponse)


@pytest.mark.unit
class TestExtractIndicationActivity:
    """Test extract_indication Temporal activity."""

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationAgent")
    def test_extract_indication_success(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test successful indication extraction."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "test_prompt.txt"
        mock_agent_class.return_value = mock_agent

        # Mock agent response
        mock_agent.invoke.return_value = {
            "messages": [
                AIMessage(content='''
                ```json
                {
                    "reasoning": "Extracted from title",
                    "selected_source": "abstract_title",
                    "generated_indication": "EGFR-positive non-small cell lung cancer",
                    "rules_retrieved": [
                        {"category": "disease", "subcategories": ["cancer"], "reason": "Identified NSCLC"}
                    ],
                    "components_identified": [
                        {"component": "EGFR+", "type": "Gene Mutation", "normalized_form": "EGFR-positive", "rule_applied": "mutation_expansion"}
                    ]
                }
                ```
                ''')
            ]
        }

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Phase 3 study of Drug X in EGFR+ NSCLC",
            session_title="Lung Cancer",
            rules_file_path="rules/indication/v3_rules.csv",
        )

        result = extract_indication(input_data)

        # Verify result
        assert result["selected_source"] == "abstract_title"
        assert result["generated_indication"] == "EGFR-positive non-small cell lung cancer"
        assert "_token_usage" in result
        assert "_llm_calls" in result

        # Verify agent was invoked correctly
        mock_agent.invoke.assert_called_once()
        call_args = mock_agent.invoke.call_args
        assert call_args[1]["abstract_title"] == input_data.abstract_title
        assert call_args[1]["session_title"] == input_data.session_title
        assert call_args[1]["abstract_id"] == input_data.abstract_id
        assert "callbacks" in call_args[1]

        # Verify EMS logging
        mock_logger.log_success.assert_called_once()
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["output"]["generated_indication"] == "EGFR-positive non-small cell lung cancer"

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationAgent")
    def test_extract_indication_no_rules(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test extraction when no rules file is provided."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = None

        mock_agent = MagicMock()
        mock_agent.prompt_file = "test_prompt.txt"
        mock_agent_class.return_value = mock_agent

        mock_agent.invoke.return_value = {
            "messages": [
                AIMessage(content='{"reasoning": "Test", "selected_source": "session_title", "generated_indication": "Lung cancer"}')
            ]
        }

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Phase 3 study",
            session_title="Lung Cancer Session",
            rules_file_path="",  # Empty rules path
        )

        result = extract_indication(input_data)

        # Verify extraction worked without rules
        assert result["selected_source"] == "session_title"
        assert result["generated_indication"] == "Lung cancer"

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationAgent")
    def test_extract_indication_agent_error(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test extraction when agent invocation fails."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "test_prompt.txt"
        mock_agent_class.return_value = mock_agent

        # Simulate agent error
        mock_agent.invoke.side_effect = Exception("LLM timeout")

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Test",
            session_title="Test",
            rules_file_path="",
        )

        with pytest.raises(Exception) as exc_info:
            extract_indication(input_data)

        assert str(exc_info.value) == "LLM timeout"

        # Verify error logging
        mock_logger.log_error.assert_called_once()
        error_call = mock_logger.log_error.call_args
        assert error_call[1]["labels"]["error_type"] == "Exception"

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationAgent")
    def test_extract_indication_no_valid_json(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test extraction when agent returns no valid JSON."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "test_prompt.txt"
        mock_agent_class.return_value = mock_agent

        mock_agent.invoke.return_value = {
            "messages": [
                AIMessage(content="No JSON here")
            ]
        }

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Test",
            session_title="Test",
            rules_file_path="",
        )

        # Should raise IndicationExtractionError for Temporal retry
        with pytest.raises(IndicationExtractionError):
            extract_indication(input_data)



@pytest.mark.unit
class TestValidateIndicationActivity:
    """Test validate_indication Temporal activity."""

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationValidationAgent")
    def test_validate_indication_pass(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test successful validation (PASS status)."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "validation_prompt.txt"
        mock_agent_class.return_value = mock_agent

        mock_agent.invoke.return_value = {
            "messages": [
                AIMessage(content='''
                ```json
                {
                    "validation_status": "PASS",
                    "issues_found": [],
                    "checks_performed": {
                        "source_selection": {"passed": true, "notes": "Correct source"},
                        "hallucination_check": {"passed": true, "notes": "No hallucinations"},
                        "omission_check": {"passed": true, "notes": "No omissions"},
                        "rule_application": {"passed": true, "notes": "Rules followed"},
                        "exclusion_compliance": {"passed": true, "notes": "Exclusions respected"},
                        "formatting_compliance": {"passed": true, "notes": "Format correct"},
                        "abbreviation_check": {"passed": true, "notes": "Full names used"}
                    },
                    "validation_reasoning": "All checks passed"
                }
                ```
                ''')
            ]
        }

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Test",
            session_title="Test",
            rules_file_path="",
        )

        extraction_result = {
            "selected_source": "abstract_title",
            "generated_indication": "Non-small cell lung cancer",
        }

        result = validate_indication(input_data, extraction_result)

        # Verify result
        assert result["validation_status"] == "PASS"
        assert result["issues_found"] == []
        assert "_token_usage" in result
        assert "_llm_calls" in result

        # Verify agent was invoked correctly
        mock_agent.invoke.assert_called_once()
        call_args = mock_agent.invoke.call_args
        assert call_args[1]["abstract_title"] == input_data.abstract_title
        assert call_args[1]["extraction_result"] == extraction_result

        # Verify EMS logging
        mock_logger.log_success.assert_called_once()
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["output"]["validation_status"] == "PASS"
        assert success_call[1]["labels"]["validation_passed"] is True
        assert success_call[1]["labels"]["num_issues_found"] == 0

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationValidationAgent")
    def test_validate_indication_review(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test validation requiring review (REVIEW status)."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "validation_prompt.txt"
        mock_agent_class.return_value = mock_agent

        mock_agent.invoke.return_value = {
            "messages": [
                AIMessage(content='''
                {
                    "validation_status": "REVIEW",
                    "issues_found": [
                        {
                            "check_name": "omission_check",
                            "severity": "medium",
                            "description": "Potential omission",
                            "evidence": "EGFR+ mentioned but not extracted"
                        }
                    ],
                    "checks_performed": {
                        "source_selection": {"passed": true, "notes": "OK"},
                        "hallucination_check": {"passed": true, "notes": "OK"},
                        "omission_check": {"passed": false, "notes": "Potential omission"},
                        "rule_application": {"passed": true, "notes": "OK"},
                        "exclusion_compliance": {"passed": true, "notes": "OK"},
                        "formatting_compliance": {"passed": true, "notes": "OK"},
                        "abbreviation_check": {"passed": true, "notes": "OK"}
                    },
                    "validation_reasoning": "Potential omission detected"
                }
                ''')
            ]
        }

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Test",
            session_title="Test",
            rules_file_path="",
        )

        extraction_result = {"selected_source": "abstract_title", "generated_indication": "NSCLC"}

        result = validate_indication(input_data, extraction_result)

        # Verify result
        assert result["validation_status"] == "REVIEW"
        assert len(result["issues_found"]) == 1

        # Verify EMS logging
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["labels"]["validation_passed"] is False
        assert success_call[1]["labels"]["num_issues_found"] == 1

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationValidationAgent")
    def test_validate_indication_error(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test validation when agent fails."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "validation_prompt.txt"
        mock_agent_class.return_value = mock_agent

        mock_agent.invoke.side_effect = Exception("Agent error")

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Test",
            session_title="Test",
            rules_file_path="",
        )

        extraction_result = {"selected_source": "abstract_title", "generated_indication": "Test"}

        with pytest.raises(Exception) as exc_info:
            validate_indication(input_data, extraction_result)

        assert str(exc_info.value) == "Agent error"

        # Verify error logging
        mock_logger.log_error.assert_called_once()

    @patch("src.temporal.activities.indication.ActivityLogger")
    @patch("src.temporal.activities.indication._get_rules_data")
    @patch("src.temporal.activities.indication.IndicationValidationAgent")
    def test_validate_indication_ems_logger_initialization(self, mock_agent_class, mock_get_rules, mock_activity_logger_class):
        """Test that ActivityLogger is properly initialized for validation."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_get_rules.return_value = []

        mock_agent = MagicMock()
        mock_agent.prompt_file = "validation_prompt.txt"
        mock_agent_class.return_value = mock_agent

        mock_agent.invoke.return_value = {
            "messages": [
                AIMessage(content='{"validation_status": "PASS", "validation_reasoning": "All checks passed", "issues_found": []}')
            ]
        }

        input_data = IndicationInput(
            abstract_id="12345",
            abstract_title="Test",
            session_title="Test",
            rules_file_path="",
        )

        extraction_result = {"selected_source": "abstract_title", "generated_indication": "Test"}

        validate_indication(input_data, extraction_result)

        # Verify ActivityLogger initialization
        mock_activity_logger_class.assert_called_once()
        init_call = mock_activity_logger_class.call_args
        assert init_call[1]["step_name"] == "indication_validation"
        assert init_call[1]["entity"] == "indication"
        assert init_call[1]["activity"] == "validate"
        assert init_call[1]["input_data"] == input_data
        assert init_call[1]["prompt_file"] == "validation_prompt.txt"
