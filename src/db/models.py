"""SQLAlchemy ORM models for entity_mapping tables.

These mirror the Django migration schema from congress-server.
"""

from sqlalchemy import (
    BigInteger, Column, DateTime, Integer, JSON, String, TIMESTAMP,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base

Base = declarative_base()

ENTITY_CHOICES = ("drug", "drug_class", "indication")
SESSION_STATUS_CHOICES = ("pending", "running", "success", "failed", "aborted")
BATCH_STATUS_CHOICES = ("pending", "running", "completed", "partial", "failed", "aborted")


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


class EntityMappingBatches(Base):
    """Batch-level metadata for entity mapping runs.

    Rows are created by the AP server. The extraction agent updates
    status and completed_at when all sessions in the batch finish.
    """

    __tablename__ = "entity_mapping_batches"

    id = Column(BigInteger, primary_key=True, index=True)
    congress_id = Column(Integer, nullable=False)
    entities = Column(JSON, nullable=True)
    rules_file_path = Column(String(512), nullable=True)
    status = Column(String(16), nullable=False, server_default="pending")
    triggered_by_id = Column(String(200), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    last_modified_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.now()
    )


class Sessions(Base):
    """Read-only model for the sessions table (owned by AP server).

    Only the columns needed for XLSX export are modelled here.
    """

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    congress_id = Column(Integer)
    title = Column(String(1000))
    abstract = Column(String(5000))
    full_abstract_text = Column(LONGTEXT, nullable=True)


class Congresses(Base):
    """Read-only model for the congresses table (owned by AP server)."""

    __tablename__ = "congresses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200))


class Users(Base):
    """Read-only model for the users table (owned by AP server)."""

    __tablename__ = "users"

    id = Column(String(200), primary_key=True, index=True)
    first_name = Column(String(200))
    last_name = Column(String(200))
    email_id = Column(String(100))
