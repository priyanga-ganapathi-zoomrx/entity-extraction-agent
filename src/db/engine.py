"""Database engine and session factory.

Fetches MySQL credentials from GCS (fc-{ENVIRONMENT}-secrets/creds.json)
and exposes a SQLAlchemy engine with NullPool for KEDA-scaled Temporal workers.

NullPool ensures each session.close() immediately closes the underlying DBAPI
connection — no persistent pool, no stale connections, no deadlock risk.
"""

from contextlib import contextmanager
from json import loads
from os import getenv
from typing import Generator
from urllib.parse import quote_plus

from google.cloud import storage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

_bucket = storage.Client().get_bucket(f"fc-{getenv('ENVIRONMENT')}-secrets")
_creds = loads(
    _bucket.get_blob("creds.json").download_as_string().decode("utf-8")
)
_mysql = _creds["mysql"]

engine = create_engine(
    "mysql+pymysql://{}:{}@{}/{}".format(
        _mysql["username"],
        quote_plus(_mysql["password"]),
        _mysql["host"],
        _mysql["fc_management_db"],
    ),
    poolclass=NullPool,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session with automatic commit/rollback/close.

    Usage::

        with get_session() as db:
            db.query(Model).filter_by(...).update(...)
            # auto-commits on exit; rolls back on exception
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
