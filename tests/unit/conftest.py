"""Unit test specific fixtures."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

# Import test fixtures
from tests.fixtures.abstracts import (
    SAMPLE_DRUG_ABSTRACT,
    SAMPLE_DRUG_CLASS_ABSTRACT,
    SAMPLE_INDICATION_ABSTRACT,
    EMPTY_ABSTRACT,
    MULTI_DRUG_ABSTRACT,
)
from tests.fixtures.llm_responses import (
    MOCK_DRUG_EXTRACTION_SINGLE,
    MOCK_DRUG_EXTRACTION_EMPTY,
    MOCK_DRUG_EXTRACTION_COMBINATION,
    MOCK_DRUG_EXTRACTION_WITH_SECONDARY,
    MOCK_DRUG_VALIDATION_PASS,
    MOCK_DRUG_VALIDATION_REVIEW,
    MOCK_DRUG_VALIDATION_FAIL,
    MOCK_DRUG_VALIDATION_WITH_MISCLASSIFICATION,
)


@pytest.fixture
def sample_drug_abstract():
    """Sample drug abstract for testing."""
    return SAMPLE_DRUG_ABSTRACT.copy()


@pytest.fixture
def sample_drug_class_abstract():
    """Sample drug class abstract for testing."""
    return SAMPLE_DRUG_CLASS_ABSTRACT.copy()


@pytest.fixture
def sample_indication_abstract():
    """Sample indication abstract for testing."""
    return SAMPLE_INDICATION_ABSTRACT.copy()


@pytest.fixture
def empty_abstract():
    """Empty abstract for testing edge cases."""
    return EMPTY_ABSTRACT.copy()


@pytest.fixture
def multi_drug_abstract():
    """Multi-drug abstract for testing."""
    return MULTI_DRUG_ABSTRACT.copy()


@pytest.fixture
def mock_drug_extraction_response():
    """Mock drug extraction LLM response."""
    return MOCK_DRUG_EXTRACTION_SINGLE


@pytest.fixture
def mock_drug_validation_pass_response():
    """Mock drug validation PASS response."""
    return MOCK_DRUG_VALIDATION_PASS


@pytest.fixture
def mock_drug_validation_review_response():
    """Mock drug validation REVIEW response."""
    return MOCK_DRUG_VALIDATION_REVIEW


@pytest.fixture
def mock_drug_validation_fail_response():
    """Mock drug validation FAIL response."""
    return MOCK_DRUG_VALIDATION_FAIL


@pytest.fixture
def mock_create_llm(mock_llm):
    """Mock create_llm function that returns configured LLM."""
    def _create_llm(*args, **kwargs):
        return mock_llm
    return _create_llm


@pytest.fixture
def mock_langfuse_handler():
    """Mock Langfuse callback handler."""
    handler = MagicMock()
    handler.trace_id = "test-trace-123"
    return handler


@pytest.fixture
def mock_activity_logger():
    """Mock ActivityLogger for EMS logging."""
    logger = MagicMock()
    logger.log_activity_start = MagicMock()
    logger.log_activity_end = MagicMock()
    logger.log_activity_error = MagicMock()
    return logger
