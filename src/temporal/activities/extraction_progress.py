"""Extraction progress activity for updating entity_mapping SQL tables.

Updates both batch-level (entity_mapping_batches_sessions) and congress-level
(entity_mapping_sessions) status in a single transaction.

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

    Performs two operations in a single transaction:
    1. UPDATE entity_mapping_batches_sessions — set status for this batch/session/entity
    2. UPSERT entity_mapping_sessions — insert or update congress-level latest status

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

    activity.logger.info(
        f"Updated extraction progress: batch={batch_id} session={session_id} "
        f"entity={entity} status={status}"
    )
