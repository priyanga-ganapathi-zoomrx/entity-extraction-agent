"""Result storage activities for saving step outputs to GCS.

These activities save pipeline step outputs (extraction results, validation results)
to GCS so they can be downloaded from the admin portal.

This is NOT checkpointing — Temporal's event history handles workflow state.
GCS is used purely to store downloadable result files.

Storage layout:
    batches/{batch_id}/abstracts/{abstract_id}/{step_name}.json
"""

from temporalio import activity

from src.agents.core.storage import get_storage_client
from src.temporal.idle_shutdown import track_activity


@activity.defn(name="save_step_output")
@track_activity
def save_step_output(
    storage_path: str,
    batch_id: int,
    abstract_id: str,
    step_name: str,
    data: dict,
) -> None:
    """Save a step's output to GCS for later download.

    Args:
        storage_path: Base storage path (gs://bucket/prefix or local path)
        batch_id: The batch ID (used in GCS path hierarchy)
        abstract_id: The abstract/session ID
        step_name: Name of the step (e.g., "drug_extraction", "drug_validation")
        data: Step output dict to save
    """
    if not storage_path:
        activity.logger.info(f"No storage path provided, skipping {step_name} save")
        return

    storage = get_storage_client(storage_path)
    path = f"batches/{batch_id}/abstracts/{abstract_id}/{step_name}.json"
    storage.upload_json(path, data)
    activity.logger.info(f"Saved {step_name} result for abstract {abstract_id} (batch {batch_id})")
