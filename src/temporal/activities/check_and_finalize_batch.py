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
    Congresses,
    EntityMappingBatches,
    EntityMappingBatchesSessions,
    EntityMappingSessions,
    Sessions,
    Users,
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

        # ── Step 4: Update batch in DB (atomic: only if still 'running') ──
        now = datetime.now(timezone.utc)
        rows_updated = db.query(EntityMappingBatches).filter_by(
            id=batch_id, status="running"
        ).update({"status": final_status, "completed_at": now})

        if rows_updated == 0:
            activity.logger.info(
                f"Batch {batch_id} already finalized by another activity, skipping"
            )
            return

        activity.logger.info(
            f"Batch {batch_id} finalized as '{final_status}'"
        )

        # Read entities while session is still open (avoids DetachedInstanceError)
        entities = batch.entities or []

        # ── Step 4b: Gather notification data (while session is open) ──
        congress_row = db.query(Congresses.name).filter_by(id=congress_id).first()
        congress_name = congress_row.name if congress_row else f"Congress {congress_id}"

        triggered_by_name = None
        if batch.triggered_by_id:
            user_row = db.query(Users.first_name, Users.last_name).filter_by(id=batch.triggered_by_id).first()
            if user_row:
                triggered_by_name = f"{user_row.first_name} {user_row.last_name}".strip()

        batch_created_at = batch.created_at

    # ── Step 5: Skip XLSX if no successful sessions ──
    if final_status in ("failed", "aborted"):
        activity.logger.info(
            f"Batch {batch_id} is '{final_status}', skipping XLSX generation"
        )
    else:
        # ── Step 6: Generate XLSX files ──
        storage = GCSStorageClient(settings.gcs.GCS_BUCKET_NAME)

        for entity in entities:
            _generate_batch_xlsx(storage, congress_id, batch_id, entity)
            _generate_congress_xlsx(storage, congress_id, entity)

        activity.logger.info(
            f"Batch {batch_id}: XLSX generation complete for entities={entities}"
        )

    # ── Step 7: Send Teams notification (after XLSX generation) ──
    try:
        _send_batch_notification(
            congress_name, batch_id, final_status, entities, batch_sessions,
            triggered_by_name, batch_created_at, now,
        )
    except Exception:
        activity.logger.warning(
            f"Batch {batch_id}: Teams notification failed, continuing",
            exc_info=True,
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


def _send_batch_notification(
    congress_name: str,
    batch_id: int,
    final_status: str,
    entities: list[str],
    batch_sessions: list,
    triggered_by: str | None,
    created_at: datetime | None,
    completed_at: datetime,
) -> None:
    """Build and send Teams notification for batch finalization."""
    from src.temporal.utils.teams_notify import format_facts, send_teams_message

    entity_counts: dict[str, dict[str, int]] = {}
    for row in batch_sessions:
        counts = entity_counts.setdefault(row.entity, {"success": 0, "failed": 0, "aborted": 0})
        if row.status in counts:
            counts[row.status] += 1

    total = len({row.session_id for row in batch_sessions})
    status_emoji = {"completed": "✅", "partial": "⚠️", "failed": "❌", "aborted": "🚫"}
    emoji = status_emoji.get(final_status, "ℹ️")

    title = f"{emoji} Entity Extraction — Batch {batch_id} {final_status.upper()}"

    # -- Batch details --
    details = {
        "Congress": congress_name,
        "Batch ID": str(batch_id),
        "Status": final_status.capitalize(),
        "Total Sessions": str(total),
    }

    if triggered_by:
        details["Triggered By"] = triggered_by

    if created_at:
        # MySQL TIMESTAMP is naive UTC; completed_at is aware UTC — normalize
        completed_naive = completed_at.replace(tzinfo=None)
        duration = completed_naive - created_at
        mins, secs = divmod(int(duration.total_seconds()), 60)
        details["Total Time"] = f"{mins}m {secs}s"

    # -- Results per entity --
    results = {}
    for entity in entities:
        counts = entity_counts.get(entity, {})
        s, f, a = counts.get("success", 0), counts.get("failed", 0), counts.get("aborted", 0)
        label = entity.replace("_", " ").title()
        parts = []
        if s:
            parts.append(f"{s} Successful")
        if f:
            parts.append(f"{f} Failed")
        if a:
            parts.append(f"{a} Aborted")
        results[label] = " | ".join(parts) if parts else "—"

    # Combine with a separator between details and results
    body = format_facts(details) + "<br>---<br>" + format_facts(results)
    send_teams_message(title, body)
