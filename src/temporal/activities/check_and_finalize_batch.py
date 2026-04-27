"""Check-and-finalize-batch activity.

Called by each workflow after updating its session status. Two responsibilities:

1. Per-entity XLSX: for each entity in completed_entities, check if all sessions
   are terminal and generate batch-level + congress-level XLSX if so.
   No batch_status gate — runs regardless of batch state to avoid a race where
   a concurrent caller finalizes the batch before this call checks its entities.
2. Batch finalization: if all sessions across all entities are terminal, update
   batch status in DB and send Teams notification.

Designed to be safe for concurrent calls (multiple workflows finishing
around the same time) and idempotent for Temporal retries.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from temporalio import activity

from src.agents.core.config import settings
from src.agents.core.ems_logger import ActivityLogger
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
    completed_entities: list[str] = field(default_factory=list)


@dataclass
class _BatchLogInput:
    """Lightweight input schema for ActivityLogger compatibility."""

    abstract_id: str
    abstract_title: str
    congress_id: int
    batch_id: int


@activity.defn(name="check_and_finalize_batch")
@track_activity
def check_and_finalize_batch(input: CheckAndFinalizeInput) -> None:
    """Check per-entity XLSX readiness and batch finalization.

    Logic:
    1. Per-entity XLSX: for each entity in completed_entities, check if all
       sessions are terminal and generate XLSX if so (no batch_status gate).
    2. Query batch — return early if status is NOT 'running'.
    3. Query batch_sessions — return early if any are 'pending' or 'running'.
    4. Determine final batch status (completed/partial/failed/aborted).
    5. Update batch status + completed_at in DB.
    6. Send Teams notification.
    """
    batch_id = input.batch_id
    congress_id = input.congress_id
    completed_entities = input.completed_entities
    start = time.time()

    log_input = _BatchLogInput(
        abstract_id=str(batch_id),
        abstract_title="",
        congress_id=congress_id,
        batch_id=batch_id,
    )
    activity_logger = ActivityLogger(
        step_name="check_and_finalize_batch",
        entity="batch",
        activity="finalize",
        input_data=log_input,
        model="n/a",
    )

    try:
        if not completed_entities:
            activity.logger.error(
                f"Batch {batch_id}: check_and_finalize_batch called with "
                "empty completed_entities — this is not a supported call pattern"
            )
            return

        # ── Step 0: Read batch metadata in a short-lived session ──
        with get_session() as db:
            batch = db.query(EntityMappingBatches).filter_by(id=batch_id).first()
            if not batch:
                activity.logger.warning(f"Batch {batch_id} not found, skipping")
                return
            entities = batch.entities or []
            batch_status = batch.status
            batch_triggered_by_id = batch.triggered_by_id
            batch_created_at = batch.created_at

        # ── Step 1: Per-entity XLSX generation ──
        # No gate on batch_status — avoids a race where a concurrent caller
        # finalizes the batch between this step and the batch-level check.
        # Safety: per-entity incomplete count check + _generate_batch_xlsx
        # returning early on zero successful sessions + idempotent GCS overwrite.
        _emit_per_entity_xlsx(batch_id, congress_id, completed_entities, batch_status)

        # ── Step 2: Batch-level finalization ──
        if batch_status == "aborted":
            activity.logger.info(
                f"Batch {batch_id} is aborted, per-entity XLSX already handled above"
            )
            return

        if batch_status != "running":
            activity.logger.info(
                f"Batch {batch_id} status is '{batch_status}' (not 'running'), skipping"
            )
            return

        # ── Running flow: queries + atomic update in their own session ──
        with get_session() as db:
            # ── Step 3: Fast check — any sessions still in progress? ──
            incomplete_count = (
                db.query(func.count(EntityMappingBatchesSessions.id))
                .filter_by(batch_id=batch_id)
                .filter(EntityMappingBatchesSessions.status.in_(("pending", "running")))
                .scalar()
            )

            if incomplete_count > 0:
                activity.logger.info(
                    f"Batch {batch_id}: {incomplete_count} sessions still in progress, skipping"
                )
                return

            # ── Step 4: Determine batch status (lightweight GROUP BY) ──
            status_counts = (
                db.query(
                    EntityMappingBatchesSessions.status,
                    func.count(EntityMappingBatchesSessions.id),
                )
                .filter_by(batch_id=batch_id)
                .group_by(EntityMappingBatchesSessions.status)
                .all()
            )

            if not status_counts:
                activity.logger.warning(f"No batch_sessions for batch {batch_id}")
                return

            status_set = {row[0] for row in status_counts}
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

            # ── Step 5: Update batch in DB (atomic: only if still 'running') ──
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

            # ── Step 5b: Gather notification data (while session is open) ──
            congress_row = db.query(Congresses.name).filter_by(id=congress_id).first()
            congress_name = congress_row.name if congress_row else f"Congress {congress_id}"

            triggered_by_name = None
            triggered_by_email = None
            if batch_triggered_by_id:
                user_row = db.query(Users.first_name, Users.last_name, Users.email_id).filter_by(id=batch_triggered_by_id).first()
                if user_row:
                    triggered_by_name = f"{user_row.first_name} {user_row.last_name}".strip()
                    triggered_by_email = user_row.email_id

            # ── Step 5c: Per-entity status counts for notification ──
            entity_status_counts = (
                db.query(
                    EntityMappingBatchesSessions.entity,
                    EntityMappingBatchesSessions.status,
                    func.count(EntityMappingBatchesSessions.id),
                )
                .filter_by(batch_id=batch_id)
                .group_by(
                    EntityMappingBatchesSessions.entity,
                    EntityMappingBatchesSessions.status,
                )
                .all()
            )

            total_sessions = (
                db.query(func.count(func.distinct(EntityMappingBatchesSessions.session_id)))
                .filter_by(batch_id=batch_id)
                .scalar()
            )

        # ── Step 6: Send Teams notification ──
        try:
            _send_batch_notification(
                congress_name, batch_id, final_status, entities,
                entity_status_counts, total_sessions,
                triggered_by_name, triggered_by_email,
                batch_created_at, now,
            )
        except Exception:
            activity.logger.warning(
                f"Batch {batch_id}: Teams notification failed, continuing",
                exc_info=True,
            )

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        activity_logger.log_error(
            error=e,
            labels={
                "error_type": type(e).__name__,
            },
            duration_ms=duration_ms,
        )
        raise


def _emit_per_entity_xlsx(
    batch_id: int,
    congress_id: int,
    completed_entities: list[str],
    batch_status: str,
) -> None:
    """Generate XLSX for each entity whose sessions are all terminal.

    For each entity in completed_entities, queries the count of pending/running
    sessions for that entity. If zero remain, generates batch-level and
    congress-level XLSX. Idempotent: GCS overwrite with identical content.

    Runs unconditionally (no batch_status gate) to avoid a race where a
    concurrent caller finalizes the batch before this entity is processed.
    """
    storage = GCSStorageClient(settings.gcs.GCS_BUCKET_NAME)

    for entity in completed_entities:
        with get_session() as db:
            incomplete = (
                db.query(func.count(EntityMappingBatchesSessions.id))
                .filter_by(batch_id=batch_id, entity=entity)
                .filter(EntityMappingBatchesSessions.status.in_(("pending", "running")))
                .scalar()
            )

        if incomplete > 0:
            activity.logger.info(
                f"Batch {batch_id} entity={entity}: {incomplete} sessions "
                "still in progress, skipping XLSX"
            )
            continue

        activity.logger.info(
            f"Batch {batch_id} entity={entity}: all sessions terminal "
            f"(batch_status={batch_status}), generating XLSX"
        )
        _generate_batch_xlsx(storage, congress_id, batch_id, entity)
        _generate_congress_xlsx(storage, congress_id, entity)

    activity.logger.info(
        f"Batch {batch_id}: per-entity XLSX step complete for {completed_entities}"
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
    output_path = f"congress/{congress_id}/batches/{batch_id}/results/congress_{congress_id}_batch_{batch_id}_{entity}.xlsx"
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
    output_path = f"congress/{congress_id}/results/congress_{congress_id}_{entity}.xlsx"
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
    entity_status_counts: list,
    total_sessions: int,
    triggered_by: str | None,
    triggered_by_email: str | None,
    created_at: datetime | None,
    completed_at: datetime,
) -> None:
    """Build and send Teams notification for batch finalization."""
    from src.temporal.utils.teams_notify import format_facts, send_teams_message

    entity_counts: dict[str, dict[str, int]] = {}
    for entity, status, count in entity_status_counts:
        counts = entity_counts.setdefault(entity, {"success": 0, "failed": 0, "aborted": 0})
        if status in counts:
            counts[status] = count

    total = total_sessions
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

    mention = None
    if triggered_by and triggered_by_email:
        details["Triggered By"] = f"<at>{triggered_by}</at>"
        mention = {"name": triggered_by, "email": triggered_by_email}
    elif triggered_by:
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
    send_teams_message(title, body, mention=mention)
