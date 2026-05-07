"""Heavy pipeline tasks.

Runs daily (cron 0 3 * * *) inside an isolated Docker container. Loads
the previous day's raw snapshots + FX rates, normalises prices to USD,
computes drift against the canonical catalogue price, classifies severity,
aggregates a report row, and writes to the processed tables idempotently.

Cuts per policy (plan_6_horas.md §13):
- infer_cause_hint removed  → cause_hint is always 'unknown'
- cleanup_old_raw removed   → no retention logic
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from prefect import get_run_logger, task
from sqlalchemy import text

from src.shared.db import db_session, get_engine


def _logger() -> logging.Logger:
    """Return the Prefect run logger when inside a run, else a stdlib fallback."""
    try:
        return get_run_logger()  # type: ignore[return-value]
    except Exception:
        return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


@task(name="load_day_window")
def load_day_window(report_date: date) -> dict[str, pd.DataFrame]:
    """Load all data required for one day's analysis from the app schema."""
    logger = _logger()

    with get_engine().connect() as conn:
        snapshots = pd.DataFrame(
            conn.execute(
                text(
                    """
                    SELECT ps.store_id, ps.sku, ps.local_price::float AS local_price,
                           ps.stock_status, ps.captured_at
                    FROM app.price_snapshots ps
                    WHERE DATE(ps.captured_at AT TIME ZONE 'UTC') = :report_date
                    ORDER BY ps.captured_at
                    """
                ),
                {"report_date": report_date},
            ).mappings().all()
        )

        fx = pd.DataFrame(
            conn.execute(
                text(
                    """
                    SELECT base_currency, quote_currency, rate::float AS rate, captured_at
                    FROM app.fx_rates
                    WHERE DATE(captured_at AT TIME ZONE 'UTC') = :report_date
                    ORDER BY captured_at
                    """
                ),
                {"report_date": report_date},
            ).mappings().all()
        )

        products = pd.DataFrame(
            conn.execute(
                text(
                    """
                    SELECT sku, canonical_price_usd::float AS canonical_price_usd
                    FROM app.products
                    WHERE is_active = true
                    """
                )
            ).mappings().all()
        )

        stores = pd.DataFrame(
            conn.execute(
                text(
                    "SELECT store_id, country, currency FROM app.stores WHERE is_active = true"
                )
            ).mappings().all()
        )

    logger.info(
        "Loaded day=%s: %d snapshots, %d fx rows, %d products, %d stores",
        report_date,
        len(snapshots),
        len(fx),
        len(products),
        len(stores),
    )
    return {"snapshots": snapshots, "fx": fx, "products": products, "stores": stores}


# ---------------------------------------------------------------------------
# Normalise
# ---------------------------------------------------------------------------


@task(name="normalize_to_usd")
def normalize_to_usd(
    snapshots: pd.DataFrame,
    fx: pd.DataFrame,
    stores: pd.DataFrame,
) -> pd.DataFrame:
    """Apply the closest-in-time FX rate to each snapshot to get local_price_usd."""
    logger = _logger()

    if snapshots.empty:
        logger.warning("No snapshots to normalise")
        return pd.DataFrame()

    # Attach currency + country via stores
    df = snapshots.merge(stores[["store_id", "currency", "country"]], on="store_id", how="left")

    parts: list[pd.DataFrame] = []
    for currency in df["currency"].dropna().unique():
        subset = df[df["currency"] == currency].copy()
        fx_curr = (
            fx[fx["quote_currency"] == currency]
            .sort_values("captured_at")[["captured_at", "rate"]]
            .copy()
        )

        if fx_curr.empty:
            logger.warning("No FX rates for %s — skipping %d rows", currency, len(subset))
            continue

        subset = subset.sort_values("captured_at")
        merged = pd.merge_asof(
            subset,
            fx_curr.rename(columns={"captured_at": "fx_at", "rate": "fx_rate"}),
            left_on="captured_at",
            right_on="fx_at",
            direction="backward",
        )
        # Rows where snapshot precedes all fx entries → fall back to earliest rate
        missing = merged["fx_rate"].isna()
        if missing.any():
            merged.loc[missing, "fx_rate"] = fx_curr["rate"].iloc[0]

        merged["local_price_usd"] = merged["local_price"] / merged["fx_rate"]
        parts.append(merged)

    if not parts:
        logger.warning("No enriched rows after FX normalisation")
        return pd.DataFrame()

    result = pd.concat(parts, ignore_index=True)
    logger.info("Normalised %d rows to USD", len(result))
    return result


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


@task(name="detect_drift")
def detect_drift(enriched: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Calculate average daily drift (%) per (store_id, sku) vs canonical USD price."""
    logger = _logger()

    if enriched.empty:
        logger.info("No enriched rows — drift DataFrame is empty")
        return pd.DataFrame()

    valid = enriched.dropna(subset=["local_price_usd"])

    grouped = (
        valid.groupby(["store_id", "sku", "country", "currency"])
        .agg(
            avg_local_price=("local_price", "mean"),
            avg_price_usd=("local_price_usd", "mean"),
        )
        .reset_index()
    )

    result = grouped.merge(products[["sku", "canonical_price_usd"]], on="sku", how="inner")

    result["drift_pct"] = (
        (result["avg_price_usd"] - result["canonical_price_usd"])
        / result["canonical_price_usd"]
        * 100.0
    )

    logger.info(
        "Drift computed for %d (store, sku) pairs — avg abs drift %.2f%%",
        len(result),
        result["drift_pct"].abs().mean() if not result.empty else 0.0,
    )
    return result


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------


@task(name="classify_severity")
def classify_severity(drifted: pd.DataFrame) -> pd.DataFrame:
    """Bin |drift_pct| into low / medium / critical severity labels."""
    logger = _logger()

    if drifted.empty:
        return drifted

    df = drifted.copy()
    abs_drift = df["drift_pct"].abs()
    df["severity"] = "low"
    df.loc[abs_drift >= 5.0, "severity"] = "medium"
    df.loc[abs_drift >= 15.0, "severity"] = "critical"

    counts = df["severity"].value_counts().to_dict()
    logger.info("Severity classified: %s", counts)
    return df


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


@task(name="aggregate_report")
def aggregate_report(
    classified: pd.DataFrame,
    fx: pd.DataFrame,
    report_date: date,
) -> dict[str, Any]:
    """Compute aggregate statistics for the daily report row."""
    logger = _logger()

    def avg_fx(quote: str) -> float | None:
        rates = fx[fx["quote_currency"] == quote]["rate"]
        return float(rates.mean()) if not rates.empty else None

    if classified.empty:
        stats: dict[str, Any] = {
            "report_date": report_date,
            "products_analyzed": 0,
            "avg_drift_pct": 0.0,
            "alerts_critical": 0,
            "alerts_medium": 0,
            "alerts_low": 0,
            "fx_rate_usd_clp_avg": avg_fx("CLP"),
            "fx_rate_usd_mxn_avg": avg_fx("MXN"),
            "fx_rate_usd_cop_avg": avg_fx("COP"),
        }
        logger.info("Empty dataset — zero-row report for %s", report_date)
        return stats

    counts = classified["severity"].value_counts()
    stats = {
        "report_date": report_date,
        "products_analyzed": len(classified),
        "avg_drift_pct": float(classified["drift_pct"].mean()),
        "alerts_critical": int(counts.get("critical", 0)),
        "alerts_medium": int(counts.get("medium", 0)),
        "alerts_low": int(counts.get("low", 0)),
        "fx_rate_usd_clp_avg": avg_fx("CLP"),
        "fx_rate_usd_mxn_avg": avg_fx("MXN"),
        "fx_rate_usd_cop_avg": avg_fx("COP"),
    }
    logger.info(
        "Report for %s: %d pairs analysed, avg_drift=%.2f%%",
        report_date,
        stats["products_analyzed"],
        stats["avg_drift_pct"],
    )
    return stats


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@task(name="write_outputs")
def write_outputs(
    report_date: date,
    classified: pd.DataFrame,
    stats: dict[str, Any],
) -> None:
    """UPSERT daily_price_reports and price_alerts idempotently.

    Re-running for the same report_date overwrites previous results (DO UPDATE).
    cause_hint is always 'unknown' — infer_cause_hint was cut per policy.
    """
    logger = _logger()

    report_stmt = text(
        """
        INSERT INTO app.daily_price_reports (
            report_date, products_analyzed, avg_drift_pct,
            alerts_critical, alerts_medium, alerts_low,
            fx_rate_usd_clp_avg, fx_rate_usd_mxn_avg, fx_rate_usd_cop_avg,
            generated_at
        ) VALUES (
            :report_date, :products_analyzed, :avg_drift_pct,
            :alerts_critical, :alerts_medium, :alerts_low,
            :fx_rate_usd_clp_avg, :fx_rate_usd_mxn_avg, :fx_rate_usd_cop_avg,
            NOW()
        )
        ON CONFLICT (report_date) DO UPDATE SET
            products_analyzed    = EXCLUDED.products_analyzed,
            avg_drift_pct        = EXCLUDED.avg_drift_pct,
            alerts_critical      = EXCLUDED.alerts_critical,
            alerts_medium        = EXCLUDED.alerts_medium,
            alerts_low           = EXCLUDED.alerts_low,
            fx_rate_usd_clp_avg  = EXCLUDED.fx_rate_usd_clp_avg,
            fx_rate_usd_mxn_avg  = EXCLUDED.fx_rate_usd_mxn_avg,
            fx_rate_usd_cop_avg  = EXCLUDED.fx_rate_usd_cop_avg,
            generated_at         = NOW()
        """
    )

    with db_session() as session:
        session.execute(report_stmt, stats)
    logger.info("Upserted daily_price_reports for %s", report_date)

    if classified.empty:
        logger.info("No alerts to write for %s", report_date)
        return

    alert_rows = [
        {
            "report_date": report_date,
            "store_id": row["store_id"],
            "sku": row["sku"],
            "avg_local_price": round(float(row["avg_local_price"]), 2),
            "avg_price_usd": round(float(row["avg_price_usd"]), 4),
            "canonical_usd": round(float(row["canonical_price_usd"]), 2),
            "drift_pct": round(float(row["drift_pct"]), 4),
            "severity": row["severity"],
            "cause_hint": "unknown",
        }
        for _, row in classified.iterrows()
    ]

    alert_stmt = text(
        """
        INSERT INTO app.price_alerts (
            report_date, store_id, sku, avg_local_price, avg_price_usd,
            canonical_usd, drift_pct, severity, cause_hint
        ) VALUES (
            :report_date, :store_id, :sku, :avg_local_price, :avg_price_usd,
            :canonical_usd, :drift_pct, :severity, :cause_hint
        )
        ON CONFLICT (report_date, store_id, sku) DO UPDATE SET
            avg_local_price = EXCLUDED.avg_local_price,
            avg_price_usd   = EXCLUDED.avg_price_usd,
            canonical_usd   = EXCLUDED.canonical_usd,
            drift_pct       = EXCLUDED.drift_pct,
            severity        = EXCLUDED.severity,
            cause_hint      = EXCLUDED.cause_hint
        """
    )

    with db_session() as session:
        session.execute(alert_stmt, alert_rows)
    logger.info("Upserted %d price_alerts for %s", len(alert_rows), report_date)
