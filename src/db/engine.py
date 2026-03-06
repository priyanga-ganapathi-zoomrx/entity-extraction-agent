"""Database engine and session factory.

Fetches MySQL credentials from GCS (fc-{ENVIRONMENT}-secrets/creds.json)
and exposes a SQLAlchemy engine with NullPool for KEDA-scaled Temporal workers.

NullPool ensures each session.close() immediately closes the underlying DBAPI
connection — no persistent pool, no stale connections, no deadlock risk.

Lazy initialization via lru_cache: the GCS fetch and engine creation happen on
the first get_session() call, not at import time. This means a transient GCS
issue at worker startup won't prevent the worker from registering activities.
If the fetch fails, lru_cache does not cache exceptions, so the next activity
invocation will retry automatically.
"""

from contextlib import contextmanager
from functools import lru_cache
from json import loads
from os import getenv
from typing import Generator
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


@lru_cache(maxsize=1)
def _get_engine():
    """Build the SQLAlchemy engine (called once, cached forever)."""
    from google.cloud import storage

    env = getenv("ENVIRONMENT")
    bucket = storage.Client().get_bucket(f"fc-{env}-secrets")
    blob = bucket.get_blob("creds.json")
    if blob is None:
        raise RuntimeError(f"creds.json not found in fc-{env}-secrets")

    mysql = loads(blob.download_as_string().decode("utf-8"))["mysql"]

    return create_engine(
        "mysql+pymysql://{}:{}@{}/{}".format(
            mysql["username"],
            quote_plus(mysql["password"]),
            mysql["ext_host"],
            mysql["fc_management_db"],
        ),
        poolclass=NullPool,
    )


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session with automatic commit/rollback/close.

    Usage::

        with get_session() as db:
            db.query(Model).filter_by(...).update(...)
            # auto-commits on exit; rolls back on exception
    """
    engine = _get_engine()
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
