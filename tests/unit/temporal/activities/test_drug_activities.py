"""Unit tests for temporal drug activities.

Tests the Temporal activity wrappers for drug extraction and validation.
Activities are thin wrappers that:
- Accept dataclass inputs
- Call underlying agent functions
- Serialize Pydantic outputs to dicts
- Track tokens and LLM calls
- Publish EMS events
"""
import pytest
from unittest.mock import MagicMock, patch, call
from src.temporal.activities.drug import (
    extract_drugs,
    validate_drugs,
)
from src.agents.drug.schemas import (
    DrugInput,
    ValidationInput,
    ExtractionResult,
    ValidationResult,
    ChecksPerformed,
    CheckResult,
    IssueFound,
)


@pytest.mark.unit
class TestExtractDrugsActivity:
    """Test extract_drugs Temporal activity."""

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._extract_drugs")
    def test_extract_drugs_success(self, mock_extract, mock_activity_logger_class):
        """Test successful drug extraction."""
        # Setup mock logger
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        # Setup mock extraction result
        extraction_result = ExtractionResult(
            primary_drugs=["Pembrolizumab"],
            secondary_drugs=[],
            comparator_drugs=["Placebo"],
            reasoning=["Step 1: Identify primary drugs", "Step 2: Identify comparators"],
        )
        mock_extract.return_value = extraction_result

        # Create input
        input_data = DrugInput(
            abstract_id="12345",
            abstract_title="Phase 3 study of pembrolizumab vs placebo in NSCLC",
        )

        # Call activity
        result = extract_drugs(input_data)

        # Verify underlying agent was called with callbacks
        mock_extract.assert_called_once()
        call_args = mock_extract.call_args
        assert call_args[0][0] == input_data
        assert "callbacks" in call_args[1]
        assert len(call_args[1]["callbacks"]) == 1  # TokenUsageCallbackHandler

        # Verify result structure
        assert result["primary_drugs"] == ["Pembrolizumab"]
        assert result["secondary_drugs"] == []
        assert result["comparator_drugs"] == ["Placebo"]
        assert result["reasoning"] == ["Step 1: Identify primary drugs", "Step 2: Identify comparators"]
        assert "_token_usage" in result
        assert "_llm_calls" in result

        # Verify EMS logging
        mock_logger.log_success.assert_called_once()
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["output"]["primary_drugs"] == ["Pembrolizumab"]
        assert success_call[1]["labels"]["num_primary_drugs"] == 1
        assert success_call[1]["labels"]["num_comparator_drugs"] == 1

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._extract_drugs")
    def test_extract_drugs_empty_result(self, mock_extract, mock_activity_logger_class):
        """Test extraction with no drugs found."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        extraction_result = ExtractionResult(
            primary_drugs=[],
            secondary_drugs=[],
            comparator_drugs=[],
            reasoning=["No drugs found in title"],
        )
        mock_extract.return_value = extraction_result

        input_data = DrugInput(
            abstract_id="12345",
            abstract_title="Study design methodology",
        )

        result = extract_drugs(input_data)

        # Verify result
        assert result["primary_drugs"] == []
        assert result["secondary_drugs"] == []
        assert result["comparator_drugs"] == []

        # Verify EMS logging with zero counts
        mock_logger.log_success.assert_called_once()
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["labels"]["num_primary_drugs"] == 0
        assert success_call[1]["labels"]["num_secondary_drugs"] == 0

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._extract_drugs")
    def test_extract_drugs_with_secondary(self, mock_extract, mock_activity_logger_class):
        """Test extraction with combination therapy (primary + secondary)."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        extraction_result = ExtractionResult(
            primary_drugs=["Pembrolizumab"],
            secondary_drugs=["Carboplatin", "Paclitaxel"],
            comparator_drugs=["Chemotherapy"],
            reasoning=["Combination therapy identified"],
        )
        mock_extract.return_value = extraction_result

        input_data = DrugInput(
            abstract_id="12345",
            abstract_title="Pembrolizumab + carboplatin + paclitaxel vs chemotherapy",
        )

        result = extract_drugs(input_data)

        # Verify result
        assert result["primary_drugs"] == ["Pembrolizumab"]
        assert result["secondary_drugs"] == ["Carboplatin", "Paclitaxel"]

        # Verify EMS labels
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["labels"]["num_secondary_drugs"] == 2

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._extract_drugs")
    def test_extract_drugs_llm_error(self, mock_extract, mock_activity_logger_class):
        """Test extraction when LLM call fails."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        # Simulate LLM error
        mock_extract.side_effect = Exception("LLM timeout")

        input_data = DrugInput(
            abstract_id="12345",
            abstract_title="Test title",
        )

        # Call should raise exception (for Temporal retry)
        with pytest.raises(Exception) as exc_info:
            extract_drugs(input_data)

        assert str(exc_info.value) == "LLM timeout"

        # Verify error logging
        mock_logger.log_error.assert_called_once()
        error_call = mock_logger.log_error.call_args
        assert error_call[1]["labels"]["error_type"] == "Exception"

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._extract_drugs")
    def test_extract_drugs_token_tracking(self, mock_extract, mock_activity_logger_class):
        """Test that token usage is tracked and returned."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        extraction_result = ExtractionResult(
            primary_drugs=["Drug A"],
            secondary_drugs=[],
            comparator_drugs=[],
            reasoning=["Test"],
        )
        mock_extract.return_value = extraction_result

        input_data = DrugInput(
            abstract_id="12345",
            abstract_title="Test title",
        )

        result = extract_drugs(input_data)

        # Verify token metadata in result
        assert "_token_usage" in result
        assert "_llm_calls" in result
        assert isinstance(result["_token_usage"], dict)
        assert isinstance(result["_llm_calls"], int)

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._extract_drugs")
    def test_extract_drugs_ems_logger_initialization(self, mock_extract, mock_activity_logger_class):
        """Test that ActivityLogger is properly initialized."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        extraction_result = ExtractionResult(
            primary_drugs=["Drug A"],
            secondary_drugs=[],
            comparator_drugs=[],
            reasoning=["Test"],
        )
        mock_extract.return_value = extraction_result

        input_data = DrugInput(
            abstract_id="12345",
            abstract_title="Test title",
        )

        extract_drugs(input_data)

        # Verify ActivityLogger was initialized correctly
        mock_activity_logger_class.assert_called_once()
        init_call = mock_activity_logger_class.call_args
        assert init_call[1]["step_name"] == "drug_extraction"
        assert init_call[1]["entity"] == "drug"
        assert init_call[1]["activity"] == "extract"
        assert init_call[1]["input_data"] == input_data
        assert init_call[1]["prompt_file"] == "inline"


@pytest.mark.unit
class TestValidateDrugsActivity:
    """Test validate_drugs Temporal activity."""

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_pass(self, mock_validate, mock_activity_logger_class):
        """Test successful validation (PASS status)."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        validation_result = ValidationResult(
            validation_status="PASS",
            validation_confidence=0.95,
            missed_drugs=[],
            issues_found=[],
            checks_performed=ChecksPerformed(
                hallucination_detection=CheckResult(passed=True, note="No hallucinations"),
                omission_detection=CheckResult(passed=True, note="No omissions"),
                rule_compliance=CheckResult(passed=True, note="Rules followed"),
                misclassification_detection=CheckResult(passed=True, note="Correct classification"),
                synonym_association_detection=CheckResult(passed=True, note="Correct synonyms"),
            ),
            validation_reasoning="All checks passed",
            grounded_search_performed=False,
        )
        mock_validate.return_value = validation_result

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Phase 3 study of pembrolizumab vs placebo",
            extraction_result={
                "primary_drugs": ["Pembrolizumab"],
                "secondary_drugs": [],
                "comparator_drugs": ["Placebo"],
            },
        )

        result = validate_drugs(input_data)

        # Verify result
        assert result["validation_status"] == "PASS"
        assert result["validation_confidence"] == 0.95
        assert result["missed_drugs"] == []
        assert result["issues_found"] == []

        # Verify EMS logging
        mock_logger.log_success.assert_called_once()
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["output"]["validation_status"] == "PASS"
        assert success_call[1]["labels"]["validation_passed"] is True
        assert success_call[1]["labels"]["num_issues_found"] == 0
        assert success_call[1]["labels"]["num_missed_drugs"] == 0

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_review(self, mock_validate, mock_activity_logger_class):
        """Test validation requiring review (REVIEW status)."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        validation_result = ValidationResult(
            validation_status="REVIEW",
            validation_confidence=0.70,
            missed_drugs=["Drug X"],
            issues_found=[
                IssueFound(
                    check_type="omission",
                    severity="medium",
                    description="Potential drug missed",
                )
            ],
            checks_performed=ChecksPerformed(
                hallucination_detection=CheckResult(passed=True, note="No hallucinations"),
                omission_detection=CheckResult(passed=False, note="Potential omission"),
                rule_compliance=CheckResult(passed=True, note="Rules followed"),
                misclassification_detection=CheckResult(passed=True, note="Correct classification"),
                synonym_association_detection=CheckResult(passed=True, note="Correct synonyms"),
            ),
            validation_reasoning="Potential omission detected",
            grounded_search_performed=True,
        )
        mock_validate.return_value = validation_result

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Study of Drug X in cancer",
            extraction_result={"primary_drugs": [], "secondary_drugs": [], "comparator_drugs": []},
        )

        result = validate_drugs(input_data)

        # Verify result
        assert result["validation_status"] == "REVIEW"
        assert result["missed_drugs"] == ["Drug X"]
        assert len(result["issues_found"]) == 1

        # Verify EMS logging
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["labels"]["validation_passed"] is False
        assert success_call[1]["labels"]["num_issues_found"] == 1
        assert success_call[1]["labels"]["num_missed_drugs"] == 1
        assert success_call[1]["labels"]["grounded_search_performed"] is True

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_fail(self, mock_validate, mock_activity_logger_class):
        """Test validation failure (FAIL status)."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        validation_result = ValidationResult(
            validation_status="FAIL",
            validation_confidence=0.50,
            missed_drugs=["Drug A", "Drug B"],
            issues_found=[
                IssueFound(
                    check_type="misclassification",
                    severity="high",
                    description="Primary drug classified as comparator",
                ),
                IssueFound(
                    check_type="omission",
                    severity="high",
                    description="Multiple drugs missed",
                ),
            ],
            checks_performed=ChecksPerformed(
                hallucination_detection=CheckResult(passed=True, note="No hallucinations"),
                omission_detection=CheckResult(passed=False, note="Multiple drugs missed"),
                rule_compliance=CheckResult(passed=True, note="Rules followed"),
                misclassification_detection=CheckResult(passed=False, note="Critical misclassification"),
                synonym_association_detection=CheckResult(passed=True, note="Correct synonyms"),
            ),
            validation_reasoning="Critical issues detected",
            grounded_search_performed=True,
        )
        mock_validate.return_value = validation_result

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Drug A + Drug B vs Drug C",
            extraction_result={"primary_drugs": ["Drug C"], "secondary_drugs": [], "comparator_drugs": []},
        )

        result = validate_drugs(input_data)

        # Verify result
        assert result["validation_status"] == "FAIL"
        assert len(result["missed_drugs"]) == 2
        assert len(result["issues_found"]) == 2

        # Verify EMS logging
        success_call = mock_logger.log_success.call_args
        assert success_call[1]["labels"]["num_issues_found"] == 2
        assert success_call[1]["labels"]["num_missed_drugs"] == 2

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_error(self, mock_validate, mock_activity_logger_class):
        """Test validation when LLM call fails."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        mock_validate.side_effect = Exception("LLM error")

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Test",
            extraction_result={"primary_drugs": ["Drug A"], "secondary_drugs": [], "comparator_drugs": []},
        )

        with pytest.raises(Exception) as exc_info:
            validate_drugs(input_data)

        assert str(exc_info.value) == "LLM error"

        # Verify error logging
        mock_logger.log_error.assert_called_once()
        error_call = mock_logger.log_error.call_args
        assert error_call[1]["labels"]["error_type"] == "Exception"

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_with_callbacks(self, mock_validate, mock_activity_logger_class):
        """Test that callbacks are passed to validation agent."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        validation_result = ValidationResult(
            validation_status="PASS",
            validation_confidence=0.95,
            missed_drugs=[],
            issues_found=[],
            checks_performed=ChecksPerformed(
                hallucination_detection=CheckResult(passed=True, note="Test"),
                omission_detection=CheckResult(passed=True, note="Test"),
                rule_compliance=CheckResult(passed=True, note="Test"),
                misclassification_detection=CheckResult(passed=True, note="Test"),
                synonym_association_detection=CheckResult(passed=True, note="Test"),
            ),
            validation_reasoning="Test",
            grounded_search_performed=False,
        )
        mock_validate.return_value = validation_result

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Test",
            extraction_result={"primary_drugs": [], "secondary_drugs": [], "comparator_drugs": []},
        )

        validate_drugs(input_data)

        # Verify callbacks were passed
        call_args = mock_validate.call_args
        assert "callbacks" in call_args[1]
        assert len(call_args[1]["callbacks"]) == 1  # TokenUsageCallbackHandler

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_token_metadata(self, mock_validate, mock_activity_logger_class):
        """Test that token usage metadata is included in result."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        validation_result = ValidationResult(
            validation_status="PASS",
            validation_confidence=0.95,
            missed_drugs=[],
            issues_found=[],
            checks_performed=ChecksPerformed(
                hallucination_detection=CheckResult(passed=True, note="Test"),
                omission_detection=CheckResult(passed=True, note="Test"),
                rule_compliance=CheckResult(passed=True, note="Test"),
                misclassification_detection=CheckResult(passed=True, note="Test"),
                synonym_association_detection=CheckResult(passed=True, note="Test"),
            ),
            validation_reasoning="Test",
            grounded_search_performed=False,
        )
        mock_validate.return_value = validation_result

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Test",
            extraction_result={"primary_drugs": [], "secondary_drugs": [], "comparator_drugs": []},
        )

        result = validate_drugs(input_data)

        # Verify token metadata
        assert "_token_usage" in result
        assert "_llm_calls" in result

    @patch("src.temporal.activities.drug.ActivityLogger")
    @patch("src.temporal.activities.drug._validate_drugs")
    def test_validate_drugs_ems_logger_initialization(self, mock_validate, mock_activity_logger_class):
        """Test that ActivityLogger is properly initialized for validation."""
        mock_logger = MagicMock()
        mock_activity_logger_class.return_value = mock_logger

        validation_result = ValidationResult(
            validation_status="PASS",
            validation_confidence=0.95,
            missed_drugs=[],
            issues_found=[],
            checks_performed=ChecksPerformed(
                hallucination_detection=CheckResult(passed=True, note="Test"),
                omission_detection=CheckResult(passed=True, note="Test"),
                rule_compliance=CheckResult(passed=True, note="Test"),
                misclassification_detection=CheckResult(passed=True, note="Test"),
                synonym_association_detection=CheckResult(passed=True, note="Test"),
            ),
            validation_reasoning="Test",
            grounded_search_performed=False,
        )
        mock_validate.return_value = validation_result

        input_data = ValidationInput(
            abstract_id="12345",
            abstract_title="Test",
            extraction_result={"primary_drugs": [], "secondary_drugs": [], "comparator_drugs": []},
        )

        validate_drugs(input_data)

        # Verify ActivityLogger initialization
        mock_activity_logger_class.assert_called_once()
        init_call = mock_activity_logger_class.call_args
        assert init_call[1]["step_name"] == "drug_validation"
        assert init_call[1]["entity"] == "drug"
        assert init_call[1]["activity"] == "validate"
        assert init_call[1]["input_data"] == input_data
        assert init_call[1]["prompt_file"] == "inline"
