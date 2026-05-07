"""Heavy pipeline flow.

Schedule: daily at 03:00 UTC (cron 0 3 * * *).
Runs inside an isolated Docker container (pricesync/heavy:latest) spawned
by the Docker work pool. Reads yesterday's raw snapshots + FX rates,
normalises to USD, detects drift, classifies severity, and writes the
processed outputs idempotently.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from prefect import flow, get_run_logger

from src.pipelines.heavy.tasks import (
    aggregate_report,
    classify_severity,
    detect_drift,
    load_day_window,
    normalize_to_usd,
    write_outputs,
)


@flow(name="heavy-price-analysis", log_prints=True)
def heavy_price_analysis(report_date: date | None = None) -> None:
    """Run the daily drift-detection and alerting pipeline.

    Args:
        report_date: The date to analyse. Defaults to yesterday (UTC).
                     Accepts explicit values to support backfill runs.
    """
    logger = get_run_logger()

    if report_date is None:
        report_date = (datetime.now(UTC) - timedelta(days=1)).date()

    logger.info("Heavy pipeline starting for report_date=%s", report_date)

    data = load_day_window(report_date=report_date)

    enriched = normalize_to_usd(
        snapshots=data["snapshots"],
        fx=data["fx"],
        stores=data["stores"],
    )

    drifted = detect_drift(enriched=enriched, products=data["products"])

    classified = classify_severity(drifted=drifted)

    stats = aggregate_report(
        classified=classified,
        fx=data["fx"],
        report_date=report_date,
    )

    write_outputs(report_date=report_date, classified=classified, stats=stats)

    logger.info(
        "Heavy pipeline done: %d pairs analysed, %d critical / %d medium / %d low",
        stats["products_analyzed"],
        stats["alerts_critical"],
        stats["alerts_medium"],
        stats["alerts_low"],
    )


if __name__ == "__main__":
    heavy_price_analysis()
