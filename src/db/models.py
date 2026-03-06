"""SQLAlchemy ORM models for entity_mapping tables.

These mirror the Django migration schema from congress-server. Only the tables
that the extraction agent needs to UPDATE/UPSERT are modelled here;
entity_mapping_batches is managed by the API layer (congress-ap-server).
"""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

ENTITY_CHOICES = ("drug", "drug_class", "indication")
SESSION_STATUS_CHOICES = ("pending", "running", "success", "failed", "aborted")


class EntityMappingBatchesSessions(Base):
    """Per-batch, per-session, per-entity progress row.

    Rows are seeded by the batch trigger API with status='pending'.
    The extraction worker updates status as workflows progress.
    """

    __tablename__ = "entity_mapping_batches_sessions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    batch_id = Column(BigInteger, nullable=False)
    session_id = Column(Integer, nullable=False)
    entity = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    last_modified_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("batch_id", "session_id", "entity", name="uq_embs_batch_session_entity"),
    )


class EntityMappingSessions(Base):
    """Congress-level latest extraction status per session per entity.

    Upserted by the extraction worker — first run inserts, subsequent
    runs (retries, new batches) update via ON DUPLICATE KEY UPDATE.
    """

    __tablename__ = "entity_mapping_sessions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    congress_id = Column(Integer, nullable=False)
    session_id = Column(Integer, nullable=False)
    entity = Column(String(16), nullable=False)
    last_batch_id = Column(BigInteger, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    last_modified_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("congress_id", "session_id", "entity", name="uq_ems_congress_session_entity"),
    )
