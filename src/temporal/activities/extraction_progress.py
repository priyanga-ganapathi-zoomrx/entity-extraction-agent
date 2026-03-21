"""Extraction progress activity for updating entity_mapping SQL tables.

Updates batch-level (entity_mapping_batches_sessions) on every call.
Updates congress-level (entity_mapping_sessions) only on terminal states
(success, failed, conditional aborted) to prevent transient states from
erasing prior results.

Uses direct GCS credential fetch + SQLAlchemy (no congress-utils dependency).
"""

from datetime import datetime, timezone

from sqlalchemy.dialects.mysql import insert
from temporalio import activity

from src.db import EntityMappingBatchesSessions, EntityMappingSessions, get_session
from src.temporal.idle_shutdown import track_activity


@activity.defn(name="update_extraction_progress")
@track_activity
def update_extraction_progress(
    batch_id: int,
    congress_id: int,
    session_id: int,
    entity: str,
    status: str,
) -> None:
    """Update extraction progress in entity_mapping SQL tables.

    Two operations:
    1. UPDATE entity_mapping_batches_sessions — ALWAYS (real-time per-batch tracking)
    2. UPSERT entity_mapping_sessions — only on terminal states:
       - "success" / "failed": always update
       - "aborted": update UNLESS current status is "success" (preserve prior success)
       - "running" / "pending": skip (don't overwrite effective status with transient state)

    Both operations are idempotent — safe for Temporal retries.

    Args:
        batch_id: The batch ID (FK to entity_mapping_batches)
        congress_id: The congress ID (FK to congresses)
        session_id: The session/abstract ID (FK to sessions)
        entity: Entity type ("drug", "drug_class", "indication")
        status: New status ("pending", "running", "success", "failed", "aborted")
    """
    now = datetime.now(timezone.utc)

    with get_session() as db:
        # ── Step 1: ALWAYS update entity_mapping_batches_sessions ──
        rows_affected = (
            db.query(EntityMappingBatchesSessions)
            .filter_by(batch_id=batch_id, session_id=session_id, entity=entity)
            .update({"status": status, "last_modified_at": now})
        )

        if rows_affected == 0:
            activity.logger.warning(
                f"No entity_mapping_batches_sessions row found for "
                f"batch={batch_id} session={session_id} entity={entity}"
            )

        # ── Step 2: Conditionally update entity_mapping_sessions ──
        if status in ("running", "pending"):
            # Skip — don't overwrite effective status with transient state
            activity.logger.info(
                f"Skipping entity_mapping_sessions update for transient status: "
                f"batch={batch_id} session={session_id} entity={entity} status={status}"
            )
        elif status == "aborted":
            # Only overwrite if current status is NOT "success"
            current = (
                db.query(EntityMappingSessions.status)
                .filter_by(congress_id=congress_id, session_id=session_id, entity=entity)
                .first()
            )
            if current and current.status == "success":
                activity.logger.info(
                    f"Preserving existing success status in entity_mapping_sessions: "
                    f"session={session_id} entity={entity} (abort skipped)"
                )
            else:
                _upsert_entity_mapping_sessions(
                    db, congress_id, session_id, entity, batch_id, status, now
                )
        else:
            # success or failed — always update
            _upsert_entity_mapping_sessions(
                db, congress_id, session_id, entity, batch_id, status, now
            )

    activity.logger.info(
        f"Updated extraction progress: batch={batch_id} session={session_id} "
        f"entity={entity} status={status}"
    )


def _upsert_entity_mapping_sessions(
    db,
    congress_id: int,
    session_id: int,
    entity: str,
    batch_id: int,
    status: str,
    now: datetime,
) -> None:
    """Insert or update entity_mapping_sessions row."""
    stmt = insert(EntityMappingSessions).values(
        congress_id=congress_id,
        session_id=session_id,
        entity=entity,
        last_batch_id=batch_id,
        status=status,
        created_at=now,
        last_modified_at=now,
    )
    stmt = stmt.on_duplicate_key_update(
        status=stmt.inserted.status,
        last_batch_id=stmt.inserted.last_batch_id,
        last_modified_at=stmt.inserted.last_modified_at,
    )
    db.execute(stmt)
