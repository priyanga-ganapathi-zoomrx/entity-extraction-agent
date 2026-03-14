"""Check-and-finalize-batch activity.

Called by each workflow after updating its session status. Checks if all
sessions in the batch are done; if so, updates batch status in DB and
generates batch-level + congress-level XLSX result files in GCS.

Designed to be safe for concurrent calls (multiple workflows finishing
around the same time) and idempotent for Temporal retries.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from temporalio import activity

from src.agents.core.config import settings
from src.agents.core.storage import GCSStorageClient
from src.db import (
    EntityMappingBatches,
    EntityMappingBatchesSessions,
    EntityMappingSessions,
    Sessions,
    get_session,
)
from src.temporal.utils.entity_mapping_export import generate_entity_xlsx
from src.temporal.idle_shutdown import track_activity


@dataclass
class CheckAndFinalizeInput:
    """Input for the check_and_finalize_batch activity."""

    batch_id: int
    congress_id: int


@activity.defn(name="check_and_finalize_batch")
@track_activity
def check_and_finalize_batch(input: CheckAndFinalizeInput) -> None:
    """Check if all sessions in a batch are done and finalize if so.

    Logic:
    1. Query batch — return early if status is NOT 'running'
    2. Query batch_sessions — return early if any are 'pending' or 'running'
    3. Determine final batch status (completed/partial/failed/aborted)
    4. Update batch status + completed_at in DB
    5. Generate & upload XLSX files (batch-level + congress-level) for each entity
    """
    batch_id = input.batch_id
    congress_id = input.congress_id

    with get_session() as db:
        # ── Step 1: Check batch status ──
        batch = db.query(EntityMappingBatches).filter_by(id=batch_id).first()
        if not batch:
            activity.logger.warning(f"Batch {batch_id} not found, skipping")
            return
        if batch.status != "running":
            activity.logger.info(
                f"Batch {batch_id} status is '{batch.status}' (not 'running'), skipping"
            )
            return

        # ── Step 2: Check if all sessions are done ──
        batch_sessions = (
            db.query(
                EntityMappingBatchesSessions.session_id,
                EntityMappingBatchesSessions.entity,
                EntityMappingBatchesSessions.status,
            )
            .filter_by(batch_id=batch_id)
            .all()
        )

        if not batch_sessions:
            activity.logger.warning(f"No batch_sessions for batch {batch_id}")
            return

        statuses = [row.status for row in batch_sessions]
        if any(s in ("pending", "running") for s in statuses):
            activity.logger.info(
                f"Batch {batch_id}: sessions still in progress, skipping"
            )
            return

        # ── Step 3: Determine batch status ──
        status_set = set(statuses)
        if status_set == {"success"}:
            final_status = "completed"
        elif status_set == {"failed"}:
            final_status = "failed"
        elif status_set == {"aborted"}:
            final_status = "aborted"
        elif "success" in status_set:
            final_status = "partial"
        else:
            final_status = "failed"

        # ── Step 4: Update batch in DB ──
        now = datetime.now(timezone.utc)
        db.query(EntityMappingBatches).filter_by(id=batch_id).update(
            {"status": final_status, "completed_at": now}
        )
        activity.logger.info(
            f"Batch {batch_id} finalized as '{final_status}'"
        )

        # Read entities while session is still open (avoids DetachedInstanceError)
        entities = batch.entities or []

    # ── Step 5: Skip XLSX if no successful sessions ──
    if final_status in ("failed", "aborted"):
        activity.logger.info(
            f"Batch {batch_id} is '{final_status}', skipping XLSX generation"
        )
        return

    # ── Step 6: Generate XLSX files ──
    storage = GCSStorageClient(settings.gcs.GCS_BUCKET_NAME)

    for entity in entities:
        _generate_batch_xlsx(storage, congress_id, batch_id, entity)
        _generate_congress_xlsx(storage, congress_id, entity)

    activity.logger.info(
        f"Batch {batch_id}: XLSX generation complete for entities={entities}"
    )


def _generate_batch_xlsx(
    storage: GCSStorageClient,
    congress_id: int,
    batch_id: int,
    entity: str,
) -> None:
    """Generate and upload batch-level XLSX."""
    with get_session() as db:
        # Get successful session_ids for this entity in this batch
        rows = (
            db.query(EntityMappingBatchesSessions.session_id)
            .filter_by(batch_id=batch_id, entity=entity, status="success")
            .all()
        )
        session_ids = [r.session_id for r in rows]

        if not session_ids:
            activity.logger.info(
                f"No successful sessions for batch={batch_id} entity={entity}"
            )
            return

        # All sessions in this batch use the same batch_id for GCS path
        session_batch_map = {sid: batch_id for sid in session_ids}

        # Get session info (title, abstract)
        session_info = _get_session_info(db, session_ids)

    xlsx_bytes = generate_entity_xlsx(
        storage, congress_id, entity, session_batch_map, session_info
    )
    output_path = f"congress/{congress_id}/batches/{batch_id}/results/{entity}.xlsx"
    storage.upload_bytes(
        output_path, xlsx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    activity.logger.info(f"Uploaded batch XLSX: {output_path} ({len(session_batch_map)} sessions)")


def _generate_congress_xlsx(
    storage: GCSStorageClient,
    congress_id: int,
    entity: str,
) -> None:
    """Generate and upload congress-level XLSX (all successful sessions)."""
    with get_session() as db:
        # Get all successful sessions for this entity in this congress
        rows = (
            db.query(
                EntityMappingSessions.session_id,
                EntityMappingSessions.last_batch_id,
            )
            .filter_by(congress_id=congress_id, entity=entity, status="success")
            .all()
        )

        if not rows:
            activity.logger.info(
                f"No successful sessions for congress={congress_id} entity={entity}"
            )
            return

        # Each session may belong to a different batch (uses last_batch_id for GCS path)
        session_batch_map = {r.session_id: r.last_batch_id for r in rows}
        session_ids = list(session_batch_map.keys())

        # Get session info (title, abstract)
        session_info = _get_session_info(db, session_ids)

    xlsx_bytes = generate_entity_xlsx(
        storage, congress_id, entity, session_batch_map, session_info
    )
    output_path = f"congress/{congress_id}/results/{entity}.xlsx"
    storage.upload_bytes(
        output_path, xlsx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    activity.logger.info(f"Uploaded congress XLSX: {output_path} ({len(session_batch_map)} sessions)")


def _get_session_info(db, session_ids: list[int]) -> dict[int, dict[str, str]]:
    """Query Sessions table for title and abstract."""
    if not session_ids:
        return {}

    sessions = (
        db.query(Sessions.id, Sessions.title, Sessions.abstract)
        .filter(Sessions.id.in_(session_ids))
        .all()
    )
    return {
        s.id: {"title": s.title or "", "abstract": s.abstract or ""}
        for s in sessions
    }
