"""Integration test: light-pipeline upserts are idempotent.

Requires a running Postgres instance (same config as .env).
Skipped automatically when the DB is not reachable.

Verifies that inserting the same (store_id, sku, captured_minute) twice
results in exactly one row in app.price_snapshots, not two.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text


@pytest.fixture
def test_snapshot_params():
    """Parameters for a single deterministic snapshot used across upsert calls."""
    return {
        "store_id": "cl",
        "sku": "PSI-001",
        "local_price": 84550.00,
        "stock_status": "instock",
        # Exact same timestamp → same captured_minute → conflict
        "captured_at": datetime(2025, 1, 1, 3, 0, 0, tzinfo=UTC),
    }


def test_upsert_snapshot_is_idempotent(db_engine, test_snapshot_params):
    """Inserting the same snapshot row twice yields exactly one row."""
    stmt = text(
        """
        INSERT INTO app.price_snapshots
            (store_id, sku, local_price, stock_status, captured_at)
        VALUES
            (:store_id, :sku, :local_price, :stock_status, :captured_at)
        ON CONFLICT (store_id, sku, captured_minute) DO NOTHING
        """
    )

    count_stmt = text(
        """
        SELECT COUNT(*) AS cnt
        FROM app.price_snapshots
        WHERE store_id = :store_id
          AND sku = :sku
          AND captured_minute = date_trunc('minute', :captured_at AT TIME ZONE 'UTC')
        """
    )

    with db_engine.begin() as conn:
        # First insert
        conn.execute(stmt, test_snapshot_params)
        # Second insert — must be a no-op
        conn.execute(stmt, test_snapshot_params)

        row = conn.execute(count_stmt, test_snapshot_params).one()
        assert row.cnt == 1, (
            f"Expected exactly 1 snapshot row after two identical inserts, found {row.cnt}"
        )

        # Clean up so repeated test runs stay clean
        conn.execute(
            text(
                """
                DELETE FROM app.price_snapshots
                WHERE store_id = :store_id AND sku = :sku
                  AND captured_minute = date_trunc('minute', :captured_at AT TIME ZONE 'UTC')
                """
            ),
            test_snapshot_params,
        )


def test_upsert_fx_rate_is_idempotent(db_engine):
    """Inserting the same FX rate twice yields exactly one row."""
    params = {
        "base_currency": "USD",
        "quote_currency": "CLP",
        "rate": 950.0,
        "captured_at": datetime(2025, 1, 1, 3, 0, 0, tzinfo=UTC),
    }

    stmt = text(
        """
        INSERT INTO app.fx_rates (base_currency, quote_currency, rate, captured_at)
        VALUES (:base_currency, :quote_currency, :rate, :captured_at)
        ON CONFLICT (base_currency, quote_currency, captured_minute) DO NOTHING
        """
    )
    count_stmt = text(
        """
        SELECT COUNT(*) AS cnt FROM app.fx_rates
        WHERE base_currency = :base_currency AND quote_currency = :quote_currency
          AND captured_minute = date_trunc('minute', :captured_at AT TIME ZONE 'UTC')
        """
    )

    with db_engine.begin() as conn:
        conn.execute(stmt, params)
        conn.execute(stmt, params)

        row = conn.execute(count_stmt, params).one()
        assert row.cnt == 1, f"Expected 1 fx row, found {row.cnt}"

        conn.execute(
            text(
                """
                DELETE FROM app.fx_rates
                WHERE base_currency = :base_currency AND quote_currency = :quote_currency
                  AND captured_minute = date_trunc('minute', :captured_at AT TIME ZONE 'UTC')
                """
            ),
            params,
        )
