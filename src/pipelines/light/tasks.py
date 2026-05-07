"""Light pipeline tasks.

These tasks are kept small and independently testable. HTTP fetches are
async and cached for 14 minutes (the cron is */15, so the cache absorbs
manual re-triggers and overlapping runs without redundant work). DB
writes are synchronous and idempotent via ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS
from sqlalchemy import text
from src.shared.config import get_settings
from src.shared.db import db_session
from src.shared.http_clients import fx_client, woocommerce_client
from src.shared.schemas import (
    FXFetchResult,
    FXRateCapture,
    PriceChangeEvent,
    StoreFetchResult,
    StorePriceCapture,
    WooCommerceProduct,
)

CACHE_TTL = timedelta(minutes=14)
QUOTE_CURRENCIES: tuple[str, ...] = ("CLP", "MXM", "COP")


# Fetch tasks (async, cached)
@task(
    name="fetch_fx_rates",
    cache_policy=INPUTS,
    cache_expiration=CACHE_TTL,
    retries=2,
    retry_delay_seconds=[2, 5],
)
async def fetch_fx_rates() -> FXFetchResult:
    """Fetch USD-base FX rates for the quote currencies we care about.

    Returns a result object even on transport errors so downstream tasks
    can persist a health log entry without aborting the flow
    """
    logger = get_run_logger()
    started = time.perf_counter()
    captured_at = datetime.now(UTC)

    try:
        async with fx_client() as client:
            response = await client.get("/latest/USD")
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("FX fetch failed: %s", exc)
        return FXFetchResult(
            rates=[],
            status_code=getattr(getattr(exc, "response", None), "status_code", 0) or 0,
            latency_ms=latency_ms,
            captured_at=captured_at,
            error=str(exc),
        )

    raw_rates: dict[str, float] = payload.get("rates", {})
    rates = [
        FXRateCapture(
            base_currency="USD",
            quote_currency=q,
            rate=Decimal(str(raw_rates[q])),
            captured_at=captured_at,
        )
        for q in QUOTE_CURRENCIES
        if q in raw_rates
    ]
    logger.info("Fetched %d FX rates in %dms", len(rates), latency_ms)
    return FXFetchResult(
        rates=rates,
        status_code=response.status_code,
        latency_ms=latency_ms,
        captured_at=captured_at,
    )


@task(
    name="fetch_store_prices",
    cache_policy=INPUTS,
    cache_expiration=CACHE_TTL,
    retries=2,
    retry_delay_seconds=[2, 5],
)
async def fetch_store_prices(store_id: str) -> StoreFetchResult:
    """Fetch the product list for a single store from the mock WooCommerce."""
    logger = get_run_logger()
    started = time.perf_counter()
    captured_at = datetime.now(UTC)

    try:
        async with woocommerce_client() as client:
            response = await client.get(
                "/wp-json/wc/v3/products",
                params={"store": store_id},
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        raw_products: list[dict[str, Any]] = response.json()
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("Store %s fetch failed: %s", store_id, exc)
        return StoreFetchResult(
            store_id=store_id,
            captures=[],
            status_code=getattr(getattr(exc, "response", None), "status_code", 0) or 0,
            latency_ms=latency_ms,
            captured_at=captured_at,
            error=str(exc),
        )

    captures: list[StorePriceCapture] = []
    for raw in raw_products:
        try:
            product = WooCommerceProduct.model_validate(raw)
        except Exception as exc:
            logger.debug("Skipping malformed product in store %s: %s", store_id, exc)
            continue
        captures.append(
            StorePriceCapture(
                store_id=store_id,
                sku=product.sku,
                local_price=product.price,
                stock_status=product.stock_status,
                captured_at=captured_at,
            )
        )

    logger.info(
        "Fetched %d products from store %s in %dms",
        len(captures),
        store_id,
        latency_ms,
    )
    return StoreFetchResult(
        store_id=store_id,
        captures=captures,
        status_code=response.status_code,
        latency_ms=latency_ms,
        captured_at=captured_at,
    )


# Health task


@task(name="validate_response_health")
def validate_response_health(
    store_results: list[StoreFetchResult],
    fx_result: FXFetchResult,
) -> None:
    """Persist per-fetch latency and status code into ``api_health_log``."""
    logger = get_run_logger()
    # api_health_log.store_id has a FK to stores — only real store_ids can be inserted.
    # FX provider health is logged here instead of persisted.
    rows: list[dict[str, Any]] = [
        {
            "store_id": r.store_id,
            "status_code": r.status_code,
            "latency_ms": r.latency_ms,
            "captured_at": r.captured_at,
        }
        for r in store_results
    ]
    logger.info(
        "FX provider health: status=%d latency=%dms",
        fx_result.status_code,
        fx_result.latency_ms,
    )

    if not rows:
        return

    stmt = text(
        """
        INSERT INTO app.api_health_log
            (store_id, status_code, latency_ms, captured_at)
        VALUES
            (:store_id, :status_code, :latency_ms, :captured_at)
        """
    )
    with db_session() as session:
        session.execute(stmt, rows)
    logger.info("Persisted %d store health rows", len(rows))


# Drift detection


@task(name="quick_drift_check")
def quick_drift_check(
    store_results: list[StoreFetchResult],
) -> list[PriceChangeEvent]:
    """Detect prices that moved more than the configured threshold.

    Compares each new capture against the latest snapshot of the same
    ``(store_id, sku)`` pair already persisted. Must run BEFORE the
    snapshot upsert so the comparison sees the prior value.
    """
    logger = get_run_logger()
    settings = get_settings()
    threshold = Decimal(str(settings.drift_threshold_pct))

    incoming: list[StorePriceCapture] = [c for r in store_results for c in r.captures]
    if not incoming:
        return []

    pairs = sorted({(c.store_id, c.sku) for c in incoming})

    # Build a parameterized VALUES list. Only the placeholder count varies;
    # all values are bound, never interpolated.
    placeholders = ", ".join(f"(:s{i}, :k{i})" for i in range(len(pairs)))
    stmt = text(
        f"""
        SELECT DISTINCT ON (s.store_id, s.sku)
            s.store_id, s.sku, s.local_price, s.captured_at
        FROM app.price_snapshots s
        JOIN (VALUES {placeholders}) AS v(store_id, sku)
            ON v.store_id = s.store_id AND v.sku = s.sku
        ORDER BY s.store_id, s.sku, s.captured_at DESC
        """
    )
    params: dict[str, Any] = {}
    for i, (store_id, sku) in enumerate(pairs):
        params[f"s{i}"] = store_id
        params[f"k{i}"] = sku

    with db_session() as session:
        rows = session.execute(stmt, params).all()

    last_seen: dict[tuple[str, str], Decimal] = {
        (row.store_id, row.sku): row.local_price for row in rows
    }

    events: list[PriceChangeEvent] = []
    for c in incoming:
        prev = last_seen.get((c.store_id, c.sku))
        if prev is None or prev == 0:
            continue
        pct = (c.local_price - prev) / prev * Decimal("100")
        if abs(pct) >= threshold:
            events.append(
                PriceChangeEvent(
                    store_id=c.store_id,
                    sku=c.sku,
                    prev_price=prev,
                    new_price=c.local_price,
                    pct_change=pct.quantize(Decimal("0.0001")),
                    detected_at=c.captured_at,
                )
            )

    logger.info(
        "Drift check: %d incoming, %d previous baselines, %d events emitted",
        len(incoming),
        len(last_seen),
        len(events),
    )
    return events


# Upsert tasks
@task(name="upsert_raw_snapshots")
def upsert_raw_snapshots(store_results: list[StoreFetchResult]) -> int:
    """Insert price snapshots; conflicts on ``captured_minute`` are skipped."""
    logger = get_run_logger()
    rows = [
        {
            "store_id": c.store_id,
            "sku": c.sku,
            "local_price": c.local_price,
            "stock_status": c.stock_status,
            "captured_at": c.captured_at,
        }
        for r in store_results
        for c in r.captures
    ]
    if not rows:
        logger.info("No snapshots to persist")
        return 0

    stmt = text(
        """
        INSERT INTO app.price_snapshots
            (store_id, sku, local_price, stock_status, captured_at)
        VALUES
            (:store_id, :sku, :local_price, :stock_status, :captured_at)
        ON CONFLICT (store_id, sku, captured_minute) DO NOTHING
        """
    )
    with db_session() as session:
        session.execute(stmt, rows)
    logger.info("Upserted snapshots: %d candidates", len(rows))
    return len(rows)


@task(name="upsert_raw_fx")
def upsert_raw_fx(fx_result: FXFetchResult) -> int:
    """Insert FX rates; conflicts on ``captured_minute`` are skipped."""
    logger = get_run_logger()
    rows = [
        {
            "base_currency": r.base_currency,
            "quote_currency": r.quote_currency,
            "rate": r.rate,
            "captured_at": r.captured_at,
        }
        for r in fx_result.rates
    ]
    if not rows:
        logger.info("No FX rates to persist")
        return 0

    stmt = text(
        """
        INSERT INTO app.fx_rates
            (base_currency, quote_currency, rate, captured_at)
        VALUES
            (:base_currency, :quote_currency, :rate, :captured_at)
        ON CONFLICT (base_currency, quote_currency, captured_minute) DO NOTHING
        """
    )
    with db_session() as session:
        session.execute(stmt, rows)
    logger.info("Upserted FX: %d candidates", len(rows))
    return len(rows)


@task(name="upsert_change_events")
def upsert_change_events(events: list[PriceChangeEvent]) -> int:
    """Insert detected price-change events idempotently."""
    logger = get_run_logger()
    if not events:
        logger.info("No change events to persist")
        return 0

    rows = [
        {
            "store_id": e.store_id,
            "sku": e.sku,
            "prev_price": e.prev_price,
            "new_price": e.new_price,
            "pct_change": e.pct_change,
            "detected_at": e.detected_at,
        }
        for e in events
    ]
    stmt = text(
        """
        INSERT INTO app.price_change_events
            (store_id, sku, prev_price, new_price, pct_change, detected_at)
        VALUES
            (:store_id, :sku, :prev_price, :new_price, :pct_change, :detected_at)
        ON CONFLICT (store_id, sku, detected_at) DO NOTHING
        """
    )
    with db_session() as session:
        session.execute(stmt, rows)
    logger.info("Upserted events: %d candidates", len(rows))
    return len(rows)
