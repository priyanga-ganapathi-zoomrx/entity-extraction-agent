"""Database engine and session factory.

Reads MySQL credentials from environment variables (DB_HOST, DB_PORT,
DB_USERNAME, DB_PASSWORD, DB_DATABASE) and delegates session management
to cgutils.db.
"""

from contextlib import contextmanager
from os import getenv
from typing import Generator

from sqlalchemy.orm import Session

_REQUIRED_VARS = ("DB_HOST", "DB_USERNAME", "DB_PASSWORD", "DB_DATABASE")


def _get_db_config() -> dict:
    """Build a cgutils db_config dict from environment variables."""
    missing = [v for v in _REQUIRED_VARS if not getenv(v)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )

    return {
        "host": getenv("DB_HOST"),
        "port": int(getenv("DB_PORT", "3306")),
        "username": getenv("DB_USERNAME"),
        "password": getenv("DB_PASSWORD"),
        "database": getenv("DB_DATABASE"),
    }


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session via cgutils with automatic commit/rollback/close."""
    from cgutils.db import get_session as _cgutils_get_session

    with _cgutils_get_session(_get_db_config()) as session:
        yield session
