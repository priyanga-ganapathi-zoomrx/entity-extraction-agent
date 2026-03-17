"""Database engine and session factory.

Fetches MySQL credentials from GCS (fc-{ENVIRONMENT}-secrets/creds.json)
and delegates session management to cgutils.db.
"""

from contextlib import contextmanager
from functools import lru_cache
from json import loads
from os import getenv
from typing import Generator

from sqlalchemy.orm import Session


@lru_cache(maxsize=1)
def _get_db_config() -> dict:
    """Fetch MySQL creds from GCS and return a cgutils db_config dict."""
    from google.cloud import storage

    env = getenv("ENVIRONMENT")
    bucket = storage.Client().get_bucket(f"fc-{env}-secrets")
    blob = bucket.get_blob("creds.json")
    if blob is None:
        raise RuntimeError(f"creds.json not found in fc-{env}-secrets")

    mysql = loads(blob.download_as_string().decode("utf-8"))["mysql"]
    return {
        "host": mysql["ext_host"],
        "port": mysql.get("port", 3306),
        "username": mysql["username"],
        "password": mysql["password"],
        "database": mysql["fc_management_db"],
    }


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session via cgutils with automatic commit/rollback/close."""
    from cgutils.db import get_session as _cgutils_get_session

    with _cgutils_get_session(_get_db_config()) as session:
        yield session
