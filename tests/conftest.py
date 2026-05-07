"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def db_engine():
    """Return a SQLAlchemy engine connected to the test database.

    Skips the test if Postgres is not reachable so the suite can run
    offline (unit tests never need this fixture).
    """
    try:
        from sqlalchemy import text

        from src.shared.db import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
