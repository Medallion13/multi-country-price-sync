"""Idempotent seeder for the ``products`` and ``stores`` catalog tables.

Uses the same in-memory data the mock API serves, guaranteeing that the
canonical USD prices and store metadata in the database match what the
HTTP mock returns. Run from the repo root:

    uv run python -m mock_woocommerce.seed
"""

from __future__ import annotations

import logging
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mock_woocommerce.data import PRODUCTS, STORES
from shared.models import Product, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _build_database_url() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ["POSTGRES_DB"]
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def seed() -> None:
    """Insert seed rows for products and stores. Idempotent via ON CONFLICT."""
    engine = create_engine(_build_database_url(), echo=False, future=True)

    product_rows = [
        {
            "sku": p.sku,
            "name": p.name,
            "canonical_price_usd": p.canonical_price_usd,
            "is_active": True,
        }
        for p in PRODUCTS
    ]
    store_rows = [
        {
            "store_id": s.store_id,
            "country": s.country,
            "currency": s.currency,
            "base_url": s.base_url,
            "is_active": True,
        }
        for s in STORES
    ]

    with Session(engine) as session, session.begin():
        product_stmt = (
            insert(Product)
            .values(product_rows)
            .on_conflict_do_nothing(index_elements=[Product.sku])
        )
        store_stmt = (
            insert(Store).values(store_rows).on_conflict_do_nothing(index_elements=[Store.store_id])
        )
        session.execute(product_stmt)
        session.execute(store_stmt)

    log.info("seeded %d products and %d stores", len(product_rows), len(store_rows))


if __name__ == "__main__":
    try:
        seed()
    except Exception:
        log.exception("seed failed")
        sys.exit(1)
