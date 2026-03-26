"""Result storage activities for saving step outputs to GCS.

These activities save pipeline step outputs (extraction results, validation results)
to GCS so they can be downloaded from the admin portal.

This is NOT checkpointing — Temporal's event history handles workflow state.
GCS is used purely to store downloadable result files.

Uses GCS_BUCKET_NAME from env (same pattern as prompts and rules).

Storage layout (grouped by entity for efficient batch+entity downloads):
    congress/{congress_id}/batches/{batch_id}/{entity}/{abstract_id}/{step_name}.json

Examples:
    congress/42/batches/7/drug/719267/extraction.json
    congress/42/batches/7/drug/719267/validation.json
    congress/42/batches/7/drug_class/719267/steps1_3.json
    congress/42/batches/7/drug_class/719267/step4.json
    congress/42/batches/7/drug_class/719267/step5.json
    congress/42/batches/7/drug_class/719267/validation.json
    congress/42/batches/7/indication/719267/extraction.json
    congress/42/batches/7/indication/719267/validation.json
"""

import time
from dataclasses import dataclass

from temporalio import activity

from src.agents.core.config import settings
from src.agents.core.ems_logger import ActivityLogger
from src.agents.core.storage import GCSStorageClient
from src.temporal.idle_shutdown import track_activity


@dataclass
class SaveStepOutputInput:
    """Input schema for ActivityLogger compatibility."""

    abstract_id: int
    abstract_title: str
    congress_id: int
    batch_id: int
    entity: str
    step_name: str


@activity.defn(name="save_step_output")
@track_activity
def save_step_output(
    congress_id: int,
    batch_id: int,
    entity: str,
    abstract_id: int,
    step_name: str,
    data: dict,
) -> None:
    """Save a step's output to GCS for later download.

    Args:
        congress_id: The congress ID (top-level folder in GCS)
        batch_id: The batch ID
        entity: Entity type ("drug", "drug_class", "indication")
        abstract_id: The abstract/session ID
        step_name: Name of the step (e.g., "extraction", "validation", "steps1_3")
        data: Step output dict to save
    """
    start = time.time()
    input_data = SaveStepOutputInput(
        abstract_id=abstract_id,
        abstract_title="",
        congress_id=congress_id,
        batch_id=batch_id,
        entity=entity,
        step_name=step_name,
    )
    activity_logger = ActivityLogger(
        step_name="save_step_output",
        entity=entity,
        activity="save",
        input_data=input_data,
        model="n/a",
    )

    try:
        storage = GCSStorageClient(settings.gcs.GCS_BUCKET_NAME)
        path = f"congress/{congress_id}/batches/{batch_id}/{entity}/{abstract_id}/{step_name}.json"
        storage.upload_json(path, data)
        activity.logger.info(
            f"Saved {entity}/{step_name} for abstract {abstract_id} (batch {batch_id})"
        )
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        activity_logger.log_error(
            error=e,
            labels={
                "error_type": type(e).__name__,
                "gcs_path": f"congress/{congress_id}/batches/{batch_id}/{entity}/{abstract_id}/{step_name}.json",
            },
            duration_ms=duration_ms,
        )
        raise
