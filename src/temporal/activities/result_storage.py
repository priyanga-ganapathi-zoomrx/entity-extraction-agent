"""Result storage activities for saving step outputs to GCS.

These activities save pipeline step outputs (extraction results, validation results)
to GCS so they can be downloaded from the admin portal.

This is NOT checkpointing — Temporal's event history handles workflow state.
GCS is used purely to store downloadable result files.

Uses GCS_BUCKET_NAME from env (same pattern as prompts and rules).

Storage layout:
    batches/{batch_id}/abstracts/{abstract_id}/{step_name}.json
"""

from temporalio import activity

from src.agents.core.config import settings
from src.agents.core.storage import GCSStorageClient
from src.temporal.idle_shutdown import track_activity


@activity.defn(name="save_step_output")
@track_activity
def save_step_output(
    batch_id: int,
    abstract_id: str,
    step_name: str,
    data: dict,
) -> None:
    """Save a step's output to GCS for later download.

    Constructs the GCS path from GCS_BUCKET_NAME env + batch_id/abstract_id/step_name.

    Args:
        batch_id: The batch ID (used in GCS path hierarchy)
        abstract_id: The abstract/session ID
        step_name: Name of the step (e.g., "drug_extraction", "drug_validation")
        data: Step output dict to save
    """
    storage = GCSStorageClient(settings.gcs.GCS_BUCKET_NAME)
    path = f"batches/{batch_id}/abstracts/{abstract_id}/{step_name}.json"
    storage.upload_json(path, data)
    activity.logger.info(f"Saved {step_name} result for abstract {abstract_id} (batch {batch_id})")
