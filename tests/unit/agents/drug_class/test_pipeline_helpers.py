"""Unit tests for drug class pipeline helper functions.

Tests the checkpoint management and status tracking helper functions
used by the pipeline orchestration logic.
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime

from src.agents.drug_class.pipeline import (
    _get_timestamp,
    _load_status,
    _save_status,
    _load_step_output,
    _save_step_output,
    _update_step_status,
    _is_step_complete,
)
from src.agents.drug_class.schemas import (
    PipelineStatus,
)


@pytest.mark.unit
class TestPipelineHelpers:
    """Test pipeline helper functions."""

    def test_get_timestamp_format(self):
        """Test that timestamp is in ISO format with Z suffix."""
        timestamp = _get_timestamp()

        # Should end with Z
        assert timestamp.endswith("Z")

        # Should be parseable as ISO datetime
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

    def test_load_status_not_exists(self):
        """Test loading status when file doesn't exist."""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = False

        result = _load_status("abstract_123", mock_storage)

        assert result is None
        mock_storage.exists.assert_called_once_with("abstract_123/status.json")

    def test_load_status_exists(self):
        """Test loading existing status."""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True

        status_data = {
            "abstract_id": "abstract_123",
            "abstract_title": "Test Study",
            "pipeline_status": "running",
            "last_completed_step": "step1_regimen",
            "steps": {
                "step1_regimen": {"status": "success", "started_at": "2024-01-01T00:00:00Z"}
            },
            "total_llm_calls": 5,
            "last_updated": "2024-01-01T00:01:00Z",
        }
        mock_storage.download_json.return_value = status_data

        result = _load_status("abstract_123", mock_storage)

        assert result is not None
        assert result.abstract_id == "abstract_123"
        assert result.abstract_title == "Test Study"
        assert result.pipeline_status == "running"
        mock_storage.download_json.assert_called_once_with("abstract_123/status.json")

    def test_load_status_error(self):
        """Test loading status with error."""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True
        mock_storage.download_json.side_effect = Exception("Download error")

        result = _load_status("abstract_123", mock_storage)

        # Should return None on error
        assert result is None

    def test_save_status(self):
        """Test saving status updates last_updated timestamp."""
        mock_storage = MagicMock()

        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
        )

        # Save status
        _save_status(status, mock_storage)

        # Verify upload was called
        mock_storage.upload_json.assert_called_once()
        call_args = mock_storage.upload_json.call_args

        # Verify path
        assert call_args[0][0] == "abstract_123/status.json"

        # Verify last_updated was set
        saved_data = call_args[0][1]
        assert "last_updated" in saved_data
        assert saved_data["last_updated"].endswith("Z")

    def test_load_step_output_not_exists(self):
        """Test loading step output when file doesn't exist."""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = False

        result = _load_step_output("abstract_123", "step1_regimen", mock_storage)

        assert result is None
        mock_storage.exists.assert_called_once_with("abstract_123/step1_regimen.json")

    def test_load_step_output_exists(self):
        """Test loading existing step output."""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True

        step_data = {
            "drug_status": {"Pembrolizumab": "success"},
            "drug_to_components": {"Pembrolizumab": ["Pembrolizumab"]},
        }
        mock_storage.download_json.return_value = step_data

        result = _load_step_output("abstract_123", "step1_regimen", mock_storage)

        assert result == step_data
        mock_storage.download_json.assert_called_once_with("abstract_123/step1_regimen.json")

    def test_load_step_output_error(self):
        """Test loading step output with error."""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True
        mock_storage.download_json.side_effect = Exception("Download error")

        result = _load_step_output("abstract_123", "step1_regimen", mock_storage)

        # Should return None on error
        assert result is None

    def test_save_step_output(self):
        """Test saving step output."""
        mock_storage = MagicMock()

        step_data = {
            "drug_status": {"Pembrolizumab": "success"},
            "drug_to_components": {"Pembrolizumab": ["Pembrolizumab"]},
        }

        _save_step_output("abstract_123", "step1_regimen", step_data, mock_storage)

        # Verify upload was called with correct path and data
        mock_storage.upload_json.assert_called_once_with(
            "abstract_123/step1_regimen.json",
            step_data
        )

    def test_update_step_status_new_step(self):
        """Test updating status for a new step."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
        )

        _update_step_status(status, "step1_regimen", "running")

        # Verify step status was added
        assert "step1_regimen" in status.steps
        assert status.steps["step1_regimen"]["status"] == "running"
        assert "started_at" in status.steps["step1_regimen"]

    def test_update_step_status_existing_step_to_success(self):
        """Test updating existing step to success."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
            steps={
                "step1_regimen": {
                    "status": "running",
                    "started_at": "2024-01-01T00:00:00Z"
                }
            }
        )

        _update_step_status(status, "step1_regimen", "success")

        # Verify step was updated to success
        assert status.steps["step1_regimen"]["status"] == "success"
        assert "completed_at" in status.steps["step1_regimen"]
        assert status.steps["step1_regimen"]["started_at"] == "2024-01-01T00:00:00Z"
        assert status.last_completed_step == "step1_regimen"

    def test_update_step_status_to_failed(self):
        """Test updating step to failed with error message."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
        )

        _update_step_status(
            status,
            "step1_regimen",
            "failed",
            error="LLM timeout"
        )

        # Verify step was marked as failed
        assert status.steps["step1_regimen"]["status"] == "failed"
        assert status.steps["step1_regimen"]["error"] == "LLM timeout"
        assert "failed_at" in status.steps["step1_regimen"]
        assert status.failed_step == "step1_regimen"
        assert status.error == "LLM timeout"

    def test_is_step_complete_not_started(self):
        """Test checking if step is complete when not started."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="pending",
        )

        result = _is_step_complete(status, "step1_regimen")

        assert result is False

    def test_is_step_complete_running(self):
        """Test checking if step is complete when running."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
            steps={
                "step1_regimen": {
                    "status": "running",
                    "started_at": "2024-01-01T00:00:00Z"
                }
            }
        )

        result = _is_step_complete(status, "step1_regimen")

        assert result is False

    def test_is_step_complete_success(self):
        """Test checking if step is complete when successful."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
            last_completed_step="step1_regimen",
            steps={
                "step1_regimen": {
                    "status": "success",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z"
                }
            }
        )

        result = _is_step_complete(status, "step1_regimen")

        assert result is True

    def test_is_step_complete_failed(self):
        """Test checking if step is complete when failed."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="failed",
            failed_step="step1_regimen",
            steps={
                "step1_regimen": {
                    "status": "failed",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z",
                    "error": "Error message"
                }
            }
        )

        result = _is_step_complete(status, "step1_regimen")

        # Failed steps are not considered complete (may retry)
        assert result is False

    def test_update_step_status_multiple_steps(self):
        """Test updating status tracks progression through multiple steps."""
        status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
        )

        # Step 1: Start
        _update_step_status(status, "step1_regimen", "running")
        assert "step1_regimen" in status.steps
        assert len(status.steps) == 1

        # Step 1: Complete
        _update_step_status(status, "step1_regimen", "success")
        assert status.steps["step1_regimen"]["status"] == "success"
        assert status.last_completed_step == "step1_regimen"

        # Step 2: Start
        _update_step_status(status, "step2_extraction", "running")
        assert "step2_extraction" in status.steps
        assert len(status.steps) == 2

        # Verify both steps are tracked
        assert "step1_regimen" in status.steps
        assert "step2_extraction" in status.steps

    def test_save_and_load_roundtrip(self):
        """Test that status can be saved and loaded correctly."""
        mock_storage = MagicMock()

        # Create a status with multiple steps
        original_status = PipelineStatus(
            abstract_id="abstract_123",
            abstract_title="Test Study",
            pipeline_status="running",
            last_completed_step="step1_regimen",
            steps={
                "step1_regimen": {
                    "status": "success",
                    "started_at": "2024-01-01T00:00:00Z",
                    "completed_at": "2024-01-01T00:01:00Z"
                },
                "step2_extraction": {
                    "status": "running",
                    "started_at": "2024-01-01T00:01:00Z"
                }
            },
            total_llm_calls=10,
        )

        # Save status
        _save_status(original_status, mock_storage)

        # Get the saved data
        saved_data = mock_storage.upload_json.call_args[0][1]

        # Simulate loading by mocking storage
        mock_storage.exists.return_value = True
        mock_storage.download_json.return_value = saved_data

        # Load status
        loaded_status = _load_status("abstract_123", mock_storage)

        # Verify data matches
        assert loaded_status.abstract_id == original_status.abstract_id
        assert loaded_status.abstract_title == original_status.abstract_title
        assert loaded_status.pipeline_status == original_status.pipeline_status
        assert loaded_status.last_completed_step == original_status.last_completed_step
        assert len(loaded_status.steps) == len(original_status.steps)
        assert "step1_regimen" in loaded_status.steps
        assert "step2_extraction" in loaded_status.steps
