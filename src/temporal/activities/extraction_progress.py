"""Extraction progress activity for updating entity_mapping SQL tables.

Updates both batch-level (entity_mapping_batches_sessions) and congress-level
(entity_mapping_sessions) status in a single transaction.

TODO: Implement actual MySQL connection and queries. Currently a stub.
"""

from temporalio import activity

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

    Updates both:
    1. entity_mapping_batches_sessions — batch-level progress
    2. entity_mapping_sessions — congress-level latest status

    Args:
        batch_id: The batch ID
        congress_id: The congress ID
        session_id: The session/abstract ID
        entity: Entity type ("drug", "drug_class", "indication")
        status: New status ("pending", "running", "success", "failed")
    """
    activity.logger.info(
        f"[STUB] update_extraction_progress: batch={batch_id} "
        f"congress={congress_id} session={session_id} "
        f"entity={entity} status={status}"
    )
