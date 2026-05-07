"""Syncronous SQLAlchemy engine and session factory

The light pipeline's DB writes are short and benefit from psycopg's pool;
async DB access is intentionally avoided to keep the stack simple.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from src.shared.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the lazily-built process-wide SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the lazily-built session factory bound to the engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            future=True,
        )
    return _session_factory


@contextmanager
def db_session() -> Iterator[Session]:
    """Yield a session inside a transaction.

    Commits on clean exit, rolls back on exception, always closes.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
