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

    # LLM settings - use PropertyMock for string values
    type(settings.llm).LLM_API_KEY = mocker.PropertyMock(return_value="test-api-key")
    type(settings.llm).LLM_BASE_URL = mocker.PropertyMock(return_value="https://test-llm.com")
    type(settings.llm).OPENAI_API_KEY = mocker.PropertyMock(return_value="test-openai-key")
    type(settings.llm).ANTHROPIC_API_KEY = mocker.PropertyMock(return_value="test-anthropic-key")
    type(settings.llm).GOOGLE_API_KEY = mocker.PropertyMock(return_value="test-google-key")
    type(settings.llm).DEFAULT_MODEL = mocker.PropertyMock(return_value="gpt-4")

    # Langfuse settings
    type(settings.langfuse).LANGFUSE_PUBLIC_KEY = mocker.PropertyMock(return_value="test-public")
    type(settings.langfuse).LANGFUSE_SECRET_KEY = mocker.PropertyMock(return_value="test-secret")
    type(settings.langfuse).LANGFUSE_HOST = mocker.PropertyMock(return_value="https://test.langfuse.com")

    # GCS settings
    type(settings.gcs).GCS_BUCKET_NAME = mocker.PropertyMock(return_value="test-bucket")
    type(settings.gcs).GCS_PROJECT_ID = mocker.PropertyMock(return_value="test-project")

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
