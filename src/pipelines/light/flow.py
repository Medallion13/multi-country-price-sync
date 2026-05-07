"""Light pipeline flow
Schedule: every 15 minutes (cron */15 * * * *).
Captures store prices and FX rates, detects price drift against the
last snapshot, and persists everything idempotently.
"""

from __future__ import annotations

import asyncio

from prefect import flow, get_run_logger
from src.pipelines.light.tasks import (
    fetch_fx_rates,
    fetch_store_prices,
    quick_drift_check,
    upsert_change_events,
    upsert_raw_fx,
    upsert_raw_snapshots,
    validate_response_health,
)
from src.shared.schemas import FXFetchResult, StoreFetchResult

STORE_IDS: tuple[str, ...] = ("cl", "mx", "co")


@flow(name="light-price-sync", log_prints=True)
async def light_price_sync() -> None:
    """Capture store prices + FX rates, detect drift, persist raw rows."""
    logger = get_run_logger()
    logger.info("Light pipeline starting for stores: %s", ", ".join(STORE_IDS))

    # Fan out — all four coroutines run concurrently.
    # asyncio.gather (not create_task) lets Prefect register task inputs
    # correctly, which is required for the INPUTS cache policy to work.
    fx_result, *store_results_raw = await asyncio.gather(
        fetch_fx_rates(),
        *[fetch_store_prices(store_id=s) for s in STORE_IDS],
    )
    fx_result: FXFetchResult = fx_result
    store_results: list[StoreFetchResult] = list(store_results_raw)

    # Drift check must run before snapshot upsert
    events = quick_drift_check(store_results)

    # Idempotent persistence. Order is independent because tables are disjoint.
    upsert_raw_snapshots(store_results)
    upsert_raw_fx(fx_result)
    upsert_change_events(events)

    # validate log api Heath
    validate_response_health(store_results, fx_result)
    logger.info(
        "Light pipeline done: %d events, %d stores fetched",
        len(events),
        sum(1 for r in store_results if not r.error),
    )


if __name__ == "__main__":
    asyncio.run(light_price_sync())
