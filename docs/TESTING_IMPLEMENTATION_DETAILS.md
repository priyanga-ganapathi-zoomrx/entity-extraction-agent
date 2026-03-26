# Unit Testing Implementation Plan for Entity Extraction Agent

## Executive Summary

This plan outlines a comprehensive testing strategy to achieve 80%+ test coverage for the entity-extraction-agent project, prioritizing agent logic (drug, indication, drug_class extraction/validation) with both fast unit tests and integration tests.

**Target:** ~19,000 lines of Python code with 0% current coverage
**Approach:** Build from agent logic outward, with dual test suite (fast unit + integration)

---

## Testing Strategy Overview

### Coverage Goals
- **Target: 80%+ overall coverage**
- Critical paths: 95%+ coverage (extraction/validation logic, Temporal workflows)
- Utility functions: 70%+ coverage
- Integration tests: End-to-end pipeline validation

### Testing Philosophy
1. **Mock by default, real APIs optional**: All tests use mocks, but support `--use-real-apis` flag for validation
2. **Dev environment integration**: MySQL and GCS tests connect to dev environment from local
3. **Fast feedback loop**: Unit tests < 1 minute, integration tests separate suite (5-10 minutes)
4. **Temporal testing**: Both workflow integration tests and activity unit tests

---

## Phase 1: Testing Infrastructure Setup

### 1.1 Dependencies and Configuration

**Add to pyproject.toml:**

```toml
[dependency-groups]
dev = [
    "pytest~=8.3.4",
    "pytest-asyncio~=0.24.0",
    "pytest-mock~=3.14.0",
    "pytest-cov~=6.0.0",
    "pytest-env~=1.1.5",
    "faker~=33.1.0",
    "freezegun~=1.5.1",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
markers = [
    "unit: Fast unit tests with mocked dependencies",
    "integration: Slower integration tests with real services",
    "temporal: Temporal workflow/activity tests",
    "slow: Tests that take >5 seconds",
    "requires_api: Tests that can optionally use real API calls",
]
addopts = [
    "-v",
    "--strict-markers",
    "--tb=short",
    "--cov=src",
    "--cov-report=term-missing",
    "--cov-report=html",
]
env = [
    "ENVIRONMENT=dev",
]

[tool.coverage.run]
source = ["src"]
omit = [
    "*/tests/*",
    "*/test_*.py",
    "*/__pycache__/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
    "if typing.TYPE_CHECKING:",
]
```

### 1.2 Test Directory Structure

```
tests/
├── __init__.py
├── conftest.py                          # Global fixtures
├── fixtures/                            # Test data fixtures
│   ├── __init__.py
│   ├── abstracts.py                     # Sample abstracts
│   ├── llm_responses.py                 # Mock LLM responses
│   ├── search_results.py                # Mock Tavily results
│   └── database_fixtures.py             # DB test data
│
├── unit/                                # Fast unit tests (<1 min total)
│   ├── __init__.py
│   ├── conftest.py                      # Unit test fixtures
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── core/
│   │   │   ├── test_llm_handler.py
│   │   │   ├── test_storage.py
│   │   │   ├── test_config.py
│   │   │   ├── test_token_tracking.py
│   │   │   └── test_ems_logger.py
│   │   │
│   │   ├── drug/
│   │   │   ├── test_extraction_agent.py
│   │   │   ├── test_validation_agent.py
│   │   │   └── test_schemas.py
│   │   │
│   │   ├── drug_class/
│   │   │   ├── test_pipeline.py
│   │   │   ├── test_step1_regimen.py
│   │   │   ├── test_step2_search.py
│   │   │   ├── test_step2_extraction.py
│   │   │   ├── test_step3_selection.py
│   │   │   ├── test_step4_explicit.py
│   │   │   ├── test_step5_consolidation.py
│   │   │   ├── test_validation.py
│   │   │   └── test_schemas.py
│   │   │
│   │   └── indication/
│   │       ├── test_extraction_agent.py
│   │       ├── test_validation_agent.py
│   │       ├── test_tools.py
│   │       └── test_schemas.py
│   │
│   ├── temporal/
│   │   ├── __init__.py
│   │   ├── test_activities/
│   │   │   ├── test_drug_activities.py
│   │   │   ├── test_drug_class_activities.py
│   │   │   ├── test_indication_activities.py
│   │   │   ├── test_result_storage.py
│   │   │   └── test_extraction_progress.py
│   │   │
│   │   └── test_client.py
│   │
│   └── db/
│       ├── test_engine.py
│       └── test_models.py
│
└── integration/                         # Integration tests (5-10 min)
    ├── __init__.py
    ├── conftest.py                      # Integration fixtures
    │
    ├── test_drug_pipeline_e2e.py
    ├── test_drug_class_pipeline_e2e.py
    ├── test_indication_pipeline_e2e.py
    │
    ├── temporal/
    │   ├── test_workflow_execution.py
    │   ├── test_workflow_pause_resume.py
    │   ├── test_workflow_signals.py
    │   └── test_state_persistence.py
    │
    ├── storage/
    │   ├── test_gcs_operations.py
    │   └── test_checkpoint_persistence.py
    │
    └── database/
        ├── test_db_transactions.py
        └── test_progress_tracking.py
```

### 1.3 Global Test Fixtures (conftest.py)

**File: `tests/conftest.py`**

```python
"""Global pytest fixtures and configuration."""
import pytest
from typing import Generator
from unittest.mock import MagicMock, Mock
from pathlib import Path

# Test data directory
TEST_DATA_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def use_real_apis(request) -> bool:
    """Control whether to use real API calls (default: False)."""
    return request.config.getoption("--use-real-apis", default=False)


@pytest.fixture(scope="session")
def dev_db_config() -> dict:
    """MySQL dev database configuration."""
    import os
    return {
        "host": os.getenv("DEV_DB_HOST", "localhost"),
        "port": int(os.getenv("DEV_DB_PORT", "3306")),
        "username": os.getenv("DEV_DB_USERNAME", "root"),
        "password": os.getenv("DEV_DB_PASSWORD", ""),
        "database": os.getenv("DEV_DB_NAME", "fc_management_db_test"),
    }


@pytest.fixture(scope="session")
def dev_gcs_config() -> dict:
    """GCS dev environment configuration."""
    import os
    return {
        "bucket": os.getenv("DEV_GCS_BUCKET", "fc-dev-congress-data"),
        "project_id": os.getenv("DEV_GCS_PROJECT_ID", ""),
    }


@pytest.fixture
def mock_llm():
    """Mock LangChain LLM with structured output."""
    llm = MagicMock()
    llm.invoke = MagicMock()
    llm.with_structured_output = MagicMock(return_value=llm)
    return llm


@pytest.fixture
def mock_storage():
    """Mock StorageClient."""
    storage = MagicMock()
    storage.download_json = MagicMock()
    storage.upload_json = MagicMock()
    storage.download_text = MagicMock()
    storage.upload_text = MagicMock()
    storage.exists = MagicMock(return_value=False)
    return storage


@pytest.fixture
def mock_langfuse():
    """Mock Langfuse client and handler."""
    from unittest.mock import MagicMock
    langfuse = MagicMock()
    handler = MagicMock()
    return {"client": langfuse, "handler": handler}


@pytest.fixture
def mock_settings(mocker):
    """Mock settings with test values."""
    settings = mocker.MagicMock()

    # LLM settings
    settings.llm.OPENAI_API_KEY = "test-key"
    settings.llm.ANTHROPIC_API_KEY = "test-key"
    settings.llm.GOOGLE_API_KEY = "test-key"
    settings.llm.DEFAULT_MODEL = "gpt-4"

    # Langfuse settings
    settings.langfuse.LANGFUSE_PUBLIC_KEY = "test-public"
    settings.langfuse.LANGFUSE_SECRET_KEY = "test-secret"
    settings.langfuse.LANGFUSE_HOST = "https://test.langfuse.com"

    # GCS settings
    settings.gcs.GCS_BUCKET_NAME = "test-bucket"
    settings.gcs.GCS_PROJECT_ID = "test-project"

    return settings


def pytest_addoption(parser):
    """Add custom pytest command-line options."""
    parser.addoption(
        "--use-real-apis",
        action="store_true",
        default=False,
        help="Use real API calls instead of mocks (for validation)",
    )
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests",
    )


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "unit: Fast unit tests with mocked dependencies"
    )
    config.addinivalue_line(
        "markers", "integration: Slower integration tests with real services"
    )
    config.addinivalue_line(
        "markers", "temporal: Temporal workflow/activity tests"
    )
    config.addinivalue_line(
        "markers", "slow: Tests that take >5 seconds"
    )
    config.addinivalue_line(
        "markers", "requires_api: Tests that can optionally use real API calls"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection based on command-line options."""
    if not config.getoption("--integration"):
        skip_integration = pytest.mark.skip(reason="need --integration option to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
```

---

## Phase 2: Agent Logic Testing (Priority 1)

### 2.1 Drug Agent Tests

**File: `tests/unit/agents/drug/test_extraction_agent.py`**

**Coverage target: 95%+**

```python
"""Unit tests for drug extraction agent."""
import pytest
from unittest.mock import MagicMock, patch
from src.agents.drug.extraction_agent import extract_drugs
from src.agents.drug.schemas import DrugInput, ExtractionResult


@pytest.fixture
def sample_drug_input():
    """Sample drug extraction input."""
    return DrugInput(
        abstract_id=123,
        abstract_title="Study of Pembrolizumab in NSCLC patients",
        session_title="Lung Cancer Session",
        full_abstract="Pembrolizumab showed efficacy...",
    )


@pytest.fixture
def mock_extraction_response():
    """Mock LLM extraction response."""
    return ExtractionResult(
        primary_drugs=["Pembrolizumab"],
        secondary_drugs=[],
        comparator_drugs=[],
    )


@pytest.mark.unit
class TestDrugExtraction:
    """Test drug extraction agent."""

    def test_extract_drugs_success(
        self,
        sample_drug_input,
        mock_extraction_response,
        mock_llm,
        mock_langfuse,
        mock_settings
    ):
        """Test successful drug extraction."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.settings', mock_settings), \
             patch('src.agents.drug.extraction_agent.get_langfuse_handler', return_value=mock_langfuse["handler"]):

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_extraction_response
            mock_create_llm.return_value = mock_llm

            # Execute
            result = extract_drugs(sample_drug_input)

            # Verify
            assert result["primary_drugs"] == ["Pembrolizumab"]
            assert result["secondary_drugs"] == []
            assert "_llm_calls" in result
            assert "_token_usage" in result
            mock_llm.invoke.assert_called_once()

    def test_extract_drugs_empty_abstract(self, mock_llm, mock_settings):
        """Test extraction with empty abstract."""
        empty_input = DrugInput(
            abstract_id=456,
            abstract_title="",
            session_title="",
            full_abstract="",
        )

        with patch('src.agents.drug.extraction_agent.create_llm', return_value=mock_llm), \
             patch('src.agents.drug.extraction_agent.settings', mock_settings):

            mock_llm.invoke.return_value = ExtractionResult(
                primary_drugs=[],
                secondary_drugs=[],
                comparator_drugs=[],
            )

            result = extract_drugs(empty_input)
            assert result["primary_drugs"] == []

    def test_extract_drugs_multiple_categories(self, sample_drug_input, mock_llm, mock_settings):
        """Test extraction with drugs in all categories."""
        with patch('src.agents.drug.extraction_agent.create_llm', return_value=mock_llm), \
             patch('src.agents.drug.extraction_agent.settings', mock_settings):

            mock_llm.invoke.return_value = ExtractionResult(
                primary_drugs=["Pembrolizumab"],
                secondary_drugs=["Nivolumab"],
                comparator_drugs=["Chemotherapy"],
            )

            result = extract_drugs(sample_drug_input)
            assert len(result["primary_drugs"]) == 1
            assert len(result["secondary_drugs"]) == 1
            assert len(result["comparator_drugs"]) == 1

    def test_extract_drugs_llm_error(self, sample_drug_input, mock_llm, mock_settings):
        """Test extraction when LLM raises an error."""
        with patch('src.agents.drug.extraction_agent.create_llm', return_value=mock_llm), \
             patch('src.agents.drug.extraction_agent.settings', mock_settings):

            mock_llm.invoke.side_effect = Exception("LLM API error")

            with pytest.raises(Exception, match="LLM API error"):
                extract_drugs(sample_drug_input)

    @pytest.mark.requires_api
    def test_extract_drugs_real_api(self, sample_drug_input, use_real_apis):
        """Test with real API (optional)."""
        if not use_real_apis:
            pytest.skip("Skipping real API test (use --use-real-apis)")

        # Use real LLM
        result = extract_drugs(sample_drug_input)
        assert isinstance(result, dict)
        assert "primary_drugs" in result


# Additional tests: validation agent, schema validation, etc.
```

**File: `tests/unit/agents/drug/test_validation_agent.py`**

Similar structure covering:
- Validation with all 5 check types
- Issue severity levels (PASS/REVIEW/FAIL)
- Grounded search integration
- Empty validation results
- Error handling

### 2.2 Drug Class Agent Tests

**File: `tests/unit/agents/drug_class/test_pipeline.py`**

**Coverage target: 95%+**

Key test scenarios:
- **Checkpoint resumption**: Test pipeline resuming from each step
- **Per-drug status tracking**: Test success/failed/pending states
- **Error propagation**: Test partial failures
- **LLM call counting**: Verify token tracking
- **Step skipping logic**: Test resume skips completed steps

**File: `tests/unit/agents/drug_class/test_step2_search.py`**

**Coverage target: 95%+**

Key test scenarios:
- **Cache hit/miss**: Test congress-level caching
- **Drug name normalization**: Test underscore conversion
- **Firms key generation**: Test sorted JSON key
- **Cache persistence**: Test save/load cycle
- **Search failures**: Test retry logic

**File: `tests/unit/agents/drug_class/test_step3_selection.py`**

**Coverage target: 95%+**

Key test scenarios:
- **Prioritization rules**: Test MoA > Chemical > Mode > Therapeutic
- **Edge case optimization**: Test 0 or 1 unique classes (no LLM call)
- **LLM selection**: Test multi-class scenarios
- **Input formats**: Test both dict and Pydantic inputs

### 2.3 Indication Agent Tests

**File: `tests/unit/agents/indication/test_extraction_agent.py`**

**Coverage target: 95%+**

Key test scenarios:
- **LangGraph state management**: Test message accumulation
- **Tool calling**: Test rules retrieval
- **Routing logic**: Test conditional edges (llm -> tools -> llm)
- **Empty results handling**: Test graceful degradation

**File: `tests/unit/agents/indication/test_tools.py`**

**Coverage target: 95%+**

Key test scenarios:
- **Rules filtering**: Test category/subcategory filtering
- **CSV parsing**: Test BOM handling, encoding
- **Caching**: Test LRU cache behavior
- **File vs in-memory modes**: Test both data sources

---

## Phase 3: Temporal Testing (Priority 2)

### 3.1 Activity Unit Tests

**File: `tests/unit/temporal/test_activities/test_drug_class_activities.py`**

**Coverage target: 90%+**

```python
"""Unit tests for drug class Temporal activities."""
import pytest
from unittest.mock import MagicMock, patch
from src.temporal.activities.drug_class import (
    step2_fetch_search_results,
    step3_selection,
    validate_drug_class_activity,
)


@pytest.mark.unit
@pytest.mark.temporal
class TestDrugClassActivities:
    """Test drug class activities."""

    def test_step2_fetch_search_results_cache_hit(self, mock_storage):
        """Test search results with cache hit."""
        # Mock cache exists
        mock_storage.exists.return_value = True
        mock_storage.download_json.return_value = {
            "drug": "pembrolizumab",
            "drug_class_results": [...],
            "firm_results": [...],
        }

        with patch('src.temporal.activities.drug_class.get_storage_client', return_value=mock_storage):
            result = step2_fetch_search_results(
                drug="Pembrolizumab",
                firms=["Merck"],
                congress_id=1,
                batch_id=2,
            )

        assert result["cache_hit"] == True
        mock_storage.exists.assert_called_once()
        mock_storage.download_json.assert_called_once()

    def test_step2_fetch_search_results_cache_miss(self, mock_storage, mocker):
        """Test search results with cache miss."""
        mock_storage.exists.return_value = False

        # Mock Tavily search
        mock_tavily = mocker.patch('src.temporal.activities.drug_class.search_drug_class')
        mock_tavily.return_value = [{"title": "...", "content": "..."}]

        with patch('src.temporal.activities.drug_class.get_storage_client', return_value=mock_storage):
            result = step2_fetch_search_results(
                drug="Pembrolizumab",
                firms=[],
                congress_id=1,
                batch_id=2,
            )

        assert result["cache_hit"] == False
        mock_storage.upload_json.assert_called_once()

    def test_step3_selection_edge_case_zero_classes(self):
        """Test selection with 0 unique classes (no LLM call)."""
        input_data = {
            "drug": "UnknownDrug",
            "extraction_details": [],
        }

        result = step3_selection(input_data)

        assert result["selected_class"] is None
        assert result["llm_called"] == False

    def test_step3_selection_edge_case_one_class(self):
        """Test selection with 1 unique class (no LLM call)."""
        input_data = {
            "drug": "Pembrolizumab",
            "extraction_details": [
                {"class_type": "MoA", "drug_class": "PD-1 Inhibitor"}
            ],
        }

        result = step3_selection(input_data)

        assert result["selected_class"] == "PD-1 Inhibitor"
        assert result["llm_called"] == False

    def test_validate_drug_class_activity_success(self, mocker):
        """Test drug class validation activity."""
        mock_validate = mocker.patch('src.temporal.activities.drug_class.validate_drug_class')
        mock_validate.return_value = MagicMock(
            overall_status="PASS",
            issues=[],
        )

        result = validate_drug_class_activity({
            "abstract_title": "...",
            "drug_classes": [...],
        })

        assert result["overall_status"] == "PASS"
        assert "_llm_calls" in result
        assert "_token_usage" in result
```

**Similar files for:**
- `test_drug_activities.py`
- `test_indication_activities.py`
- `test_result_storage.py`
- `test_extraction_progress.py`

### 3.2 Workflow Integration Tests

**File: `tests/integration/temporal/test_workflow_execution.py`**

**Coverage target: 85%+**

```python
"""Integration tests for Temporal workflow execution."""
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from src.temporal.workflows.abstract_extraction import AbstractExtractionWorkflow
from src.temporal.schemas.workflow import AbstractExtractionInput


@pytest.mark.integration
@pytest.mark.temporal
@pytest.mark.asyncio
class TestWorkflowExecution:
    """Test workflow execution with Temporal test environment."""

    async def test_drug_pipeline_success(self, mocker):
        """Test successful drug pipeline execution."""
        # Mock activities
        async def mock_extract_drugs(input_data):
            return {"primary_drugs": ["Pembrolizumab"], "_llm_calls": 1, "_token_usage": {}}

        async def mock_validate_drugs(input_data):
            return {"overall_status": "PASS", "_llm_calls": 1, "_token_usage": {}}

        async def mock_save_step_output(*args, **kwargs):
            pass

        async def mock_update_progress(*args, **kwargs):
            pass

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-workflows",
                workflows=[AbstractExtractionWorkflow],
                activities=[
                    mock_extract_drugs,
                    mock_validate_drugs,
                    mock_save_step_output,
                    mock_update_progress,
                ],
            ):
                input_data = AbstractExtractionInput(
                    abstract_id=123,
                    abstract_title="Study of Pembrolizumab",
                    entity="drug",
                    congress_id=1,
                    batch_id=2,
                )

                result = await env.client.execute_workflow(
                    AbstractExtractionWorkflow.run,
                    input_data,
                    id="test-drug-workflow",
                    task_queue="test-workflows",
                )

                assert result.completed == True
                assert result.drug_output is not None

    async def test_drug_class_pipeline_with_cache(self):
        """Test drug class pipeline with search cache hit."""
        # Mock activities with cache simulation
        # ... similar structure

    async def test_indication_pipeline_with_rules(self):
        """Test indication pipeline with rules loading."""
        # ... similar structure
```

**File: `tests/integration/temporal/test_workflow_pause_resume.py`**

**Coverage target: 90%+**

Key test scenarios:
- **Pause on failure**: Test workflow pauses when activity fails
- **Retry signal**: Test workflow resumes after retry signal
- **Abort signal**: Test workflow aborts gracefully
- **State persistence**: Test `_completed_steps` and `_dc_per_drug_data` persist
- **Partial completion**: Test resume skips completed work

---

## Phase 4: Core Utilities Testing (Priority 3)

### 4.1 Storage Tests

**File: `tests/unit/agents/core/test_storage.py`**

**Coverage target: 90%+**

```python
"""Unit tests for storage abstraction."""
import pytest
from pathlib import Path
from src.agents.core.storage import (
    LocalStorageClient,
    GCSStorageClient,
    get_storage_client,
    parse_gcs_path,
)


@pytest.mark.unit
class TestLocalStorage:
    """Test local storage client."""

    def test_upload_download_json(self, tmp_path):
        """Test JSON upload and download."""
        storage = LocalStorageClient(str(tmp_path))

        test_data = {"key": "value", "number": 42}
        storage.upload_json("test.json", test_data)

        result = storage.download_json("test.json")
        assert result == test_data

    def test_exists_true(self, tmp_path):
        """Test exists returns True for existing file."""
        storage = LocalStorageClient(str(tmp_path))
        storage.upload_json("exists.json", {})

        assert storage.exists("exists.json") == True

    def test_exists_false(self, tmp_path):
        """Test exists returns False for missing file."""
        storage = LocalStorageClient(str(tmp_path))
        assert storage.exists("missing.json") == False


@pytest.mark.unit
class TestGCSStorage:
    """Test GCS storage client (mocked)."""

    def test_parse_gcs_path(self):
        """Test GCS path parsing."""
        bucket, prefix = parse_gcs_path("gs://my-bucket/path/to/file.json")
        assert bucket == "my-bucket"
        assert prefix == "path/to/file.json"

    def test_parse_gcs_path_no_prefix(self):
        """Test GCS path parsing without prefix."""
        bucket, prefix = parse_gcs_path("gs://my-bucket")
        assert bucket == "my-bucket"
        assert prefix == ""

    def test_get_storage_client_gcs(self):
        """Test factory returns GCS client for gs:// paths."""
        client = get_storage_client("gs://test-bucket/prefix")
        assert isinstance(client, GCSStorageClient)

    def test_get_storage_client_local(self):
        """Test factory returns local client for filesystem paths."""
        client = get_storage_client("output/data")
        assert isinstance(client, LocalStorageClient)


@pytest.mark.integration
class TestGCSStorageIntegration:
    """Integration tests with real GCS (dev environment)."""

    def test_gcs_upload_download(self, dev_gcs_config):
        """Test GCS upload and download with dev bucket."""
        storage = GCSStorageClient(
            bucket_name=dev_gcs_config["bucket"],
            base_prefix="tests/integration",
        )

        test_data = {"test": "data", "timestamp": "2025-03-13"}
        storage.upload_json("test.json", test_data)

        result = storage.download_json("test.json")
        assert result == test_data

        # Cleanup
        blob = storage.bucket.blob(storage._get_blob_path("test.json"))
        blob.delete()
```

### 4.2 Database Tests

**File: `tests/unit/db/test_engine.py`**

**Coverage target: 85%+**

```python
"""Unit tests for database engine."""
import pytest
from unittest.mock import MagicMock, patch
from src.db.engine import get_session, _get_engine


@pytest.mark.unit
class TestDatabaseEngine:
    """Test database engine (mocked)."""

    def test_get_session_commit_on_success(self, mocker):
        """Test session commits on successful exit."""
        mock_session = MagicMock()
        mock_engine = MagicMock()

        with patch('src.db.engine._get_engine', return_value=mock_engine), \
             patch('src.db.engine.sessionmaker', return_value=lambda: mock_session):

            with get_session() as session:
                session.execute("SELECT 1")

            mock_session.commit.assert_called_once()
            mock_session.close.assert_called_once()

    def test_get_session_rollback_on_error(self, mocker):
        """Test session rolls back on exception."""
        mock_session = MagicMock()
        mock_engine = MagicMock()

        with patch('src.db.engine._get_engine', return_value=mock_engine), \
             patch('src.db.engine.sessionmaker', return_value=lambda: mock_session):

            with pytest.raises(ValueError):
                with get_session() as session:
                    raise ValueError("Test error")

            mock_session.rollback.assert_called_once()
            mock_session.close.assert_called_once()


@pytest.mark.integration
class TestDatabaseIntegration:
    """Integration tests with dev MySQL database."""

    def test_session_crud_operations(self, dev_db_config):
        """Test CRUD operations with dev database."""
        from src.db.engine import get_session
        from src.db.models import EntityMappingBatchesSessions

        with get_session() as db:
            # Create
            session_row = EntityMappingBatchesSessions(
                batch_id=9999,
                session_id=123,
                entity="drug",
                status="pending",
            )
            db.add(session_row)
            db.flush()

            # Read
            result = db.query(EntityMappingBatchesSessions).filter_by(
                batch_id=9999, session_id=123
            ).first()
            assert result is not None
            assert result.entity == "drug"

            # Update
            result.status = "success"
            db.flush()

            # Delete
            db.delete(result)
```

---

## Phase 5: Schema Testing

### 5.1 Pydantic Model Tests

**File: `tests/unit/agents/drug_class/test_schemas.py`**

**Coverage target: 80%+**

```python
"""Unit tests for drug class schemas."""
import pytest
from pydantic import ValidationError
from src.agents.drug_class.schemas.outputs import Step2Output, DrugExtractionResult
from src.agents.drug_class.schemas.llm_responses import DrugClassLLMResponse


@pytest.mark.unit
class TestDrugClassSchemas:
    """Test drug class Pydantic schemas."""

    def test_step2_output_per_drug_tracking(self):
        """Test per-drug status tracking in Step2Output."""
        output = Step2Output(
            drugs=["DrugA", "DrugB", "DrugC"],
            drug_status={},
            extraction_per_drug={},
        )

        # Mark success
        output.mark_success("DrugA", {"class": "..."})
        assert output.drug_status["DrugA"] == "success"
        assert output.is_complete() == False

        # Mark failed
        output.mark_failed("DrugB", "Error message")
        assert output.drug_status["DrugB"] == "failed"

        # Get pending
        pending = output.get_pending_drugs()
        assert pending == ["DrugC"]

        # Complete
        output.mark_success("DrugC", {"class": "..."})
        assert output.is_complete() == True

    def test_llm_response_to_output_conversion(self):
        """Test LLM response converts to output format."""
        llm_response = DrugClassLLMResponse(
            extraction_details=[
                {
                    "class_type": "MoA",
                    "drug_class": "PD-1 Inhibitor",
                    "confidence": "High",
                }
            ]
        )

        result = llm_response.to_extraction_result()

        assert isinstance(result, DrugExtractionResult)
        assert len(result.extraction_details) == 1

    def test_schema_validation_error(self):
        """Test schema raises validation error for invalid data."""
        with pytest.raises(ValidationError):
            DrugExtractionResult(
                extraction_details="invalid"  # Should be list
            )
```

---

## Phase 6: Test Fixtures and Mock Data

### 6.1 Sample Test Data

**File: `tests/fixtures/abstracts.py`**

```python
"""Sample abstracts for testing."""

SAMPLE_DRUG_ABSTRACT = {
    "abstract_id": 123,
    "abstract_title": "Efficacy of Pembrolizumab in Advanced NSCLC",
    "session_title": "Lung Cancer",
    "full_abstract": """
Background: Non-small cell lung cancer (NSCLC) remains a leading cause of cancer mortality.
Methods: This phase 3 trial evaluated Pembrolizumab vs chemotherapy in 500 patients.
Results: Pembrolizumab showed superior overall survival (OS: 18.2 vs 12.1 months).
Conclusion: Pembrolizumab is effective in advanced NSCLC.
    """.strip(),
}

SAMPLE_DRUG_CLASS_ABSTRACT = {
    "abstract_id": 456,
    "abstract_title": "Combination of Pembrolizumab + Carboplatin in NSCLC",
    "session_title": "Combination Therapy",
    "full_abstract": """
Background: Combination therapy may improve outcomes.
Methods: Pembrolizumab + Carboplatin was tested in 300 patients.
Results: The combination showed ORR of 65%.
    """.strip(),
}

SAMPLE_INDICATION_ABSTRACT = {
    "abstract_id": 789,
    "abstract_title": "Pembrolizumab in Metastatic Melanoma",
    "session_title": "Melanoma",
    "full_abstract": """
Background: Melanoma treatment has evolved with immunotherapy.
Methods: 200 patients with metastatic melanoma received Pembrolizumab.
Results: Response rate was 40% with durable responses.
    """.strip(),
}
```

**File: `tests/fixtures/llm_responses.py`**

```python
"""Mock LLM responses for testing."""
from src.agents.drug.schemas import ExtractionResult, ValidationResult


MOCK_DRUG_EXTRACTION = ExtractionResult(
    primary_drugs=["Pembrolizumab"],
    secondary_drugs=[],
    comparator_drugs=["Chemotherapy"],
)

MOCK_DRUG_VALIDATION_PASS = ValidationResult(
    overall_status="PASS",
    issues=[],
    checks_performed={
        "hallucination": {"status": "PASS", "issues": []},
        "omission": {"status": "PASS", "issues": []},
        "rule_compliance": {"status": "PASS", "issues": []},
        "misclassification": {"status": "PASS", "issues": []},
        "synonym": {"status": "PASS", "issues": []},
    },
)

# Additional mock responses...
```

---

## Phase 7: CI/CD Integration

### 7.1 Test Execution Scripts

**File: `scripts/run_tests.sh`**

```bash
#!/bin/bash
# Run unit tests (fast)
echo "Running unit tests..."
pytest tests/unit -m unit --cov=src --cov-report=term-missing

# Check coverage threshold
echo "Checking coverage threshold..."
pytest --cov=src --cov-fail-under=80 tests/unit
```

**File: `scripts/run_integration_tests.sh`**

```bash
#!/bin/bash
# Run integration tests (slower)
echo "Running integration tests..."
pytest tests/integration -m integration --integration
```

### 7.2 Pre-commit Hook

**File: `.pre-commit-config.yaml`** (optional)

```yaml
repos:
  - repo: local
    hooks:
      - id: pytest-unit
        name: pytest-unit
        entry: pytest tests/unit -m unit --exitfirst
        language: system
        pass_filenames: false
        always_run: true
```

---

## Implementation Order

### Week 1-2: Infrastructure + Drug Agent
1. Set up test infrastructure (conftest.py, fixtures)
2. Implement drug extraction tests
3. Implement drug validation tests
4. Target: 95%+ coverage for drug agent

### Week 3-4: Drug Class Agent
1. Implement pipeline orchestration tests
2. Implement step 1-5 tests
3. Implement search caching tests
4. Implement validation tests
5. Target: 95%+ coverage for drug_class agent

### Week 5: Indication Agent
1. Implement extraction agent tests (LangGraph)
2. Implement validation tests
3. Implement tools tests (rules loading)
4. Target: 95%+ coverage for indication agent

### Week 6: Temporal Activities
1. Implement activity unit tests (all 15+ activities)
2. Implement result storage tests
3. Implement progress tracking tests
4. Target: 90%+ coverage for activities

### Week 7: Temporal Workflows
1. Implement workflow execution tests
2. Implement pause/resume tests
3. Implement state persistence tests
4. Target: 85%+ coverage for workflows

### Week 8: Core Utilities + Integration
1. Implement storage tests
2. Implement database tests
3. Implement LLM handler tests
4. Implement end-to-end integration tests
5. Target: 80%+ overall coverage

---

## Success Metrics

### Coverage Targets
- **Overall: 80%+**
- Drug agent: 95%+
- Drug class agent: 95%+
- Indication agent: 95%+
- Temporal activities: 90%+
- Temporal workflows: 85%+
- Core utilities: 80%+

### Test Execution Performance
- Unit tests: < 1 minute
- Integration tests: 5-10 minutes
- Total test suite: < 15 minutes

### Quality Gates
- All tests pass before merge
- No decrease in coverage
- Critical paths: 100% coverage
- Integration tests: Pass with dev environment

---

## Risk Mitigation

### Risk 1: LLM Response Variability
- **Mitigation**: Use structured output validation in tests
- Mock LLM responses with representative samples
- Test edge cases (empty, malformed, error responses)

### Risk 2: Temporal Test Complexity
- **Mitigation**: Start with activity unit tests (simpler)
- Use Temporal's test framework for workflows
- Separate fast unit tests from slow integration tests

### Risk 3: Dev Environment Dependencies
- **Mitigation**: Use environment variables for configuration
- Provide clear setup documentation
- Mock by default, real services optional

### Risk 4: Test Maintenance Burden
- **Mitigation**: Use fixtures and parametrize to reduce duplication
- Keep tests focused and isolated
- Regular refactoring to keep tests maintainable

---

## Next Steps

After plan approval:
1. Create test directory structure
2. Set up pytest configuration
3. Implement global fixtures
4. Start with drug agent tests (highest priority)
5. Iterate through remaining components

**Estimated Total Effort:** 8 weeks (1 developer full-time)
**Deliverables:**
- 200+ test files
- 80%+ code coverage
- CI/CD integration
- Documentation for running tests
