"""Unit tests for the heavy pipeline's drift-detection logic.

All tests are pure-pandas — no DB, no Prefect server required.
We import the underlying functions directly (bypassing @task) so we
can call them without a flow run context.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.pipelines.heavy.tasks import classify_severity, detect_drift, normalize_to_usd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"store_id": "cl", "country": "CL", "currency": "CLP"},
            {"store_id": "mx", "country": "MX", "currency": "MXN"},
            {"store_id": "co", "country": "CO", "currency": "COP"},
        ]
    )


def _products() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"sku": "PSI-001", "canonical_price_usd": 89.0},
            {"sku": "PSI-002", "canonical_price_usd": 149.0},
        ]
    )


def _fx(rates: dict[str, float] | None = None) -> pd.DataFrame:
    base = {"CLP": 950.0, "MXN": 18.0, "COP": 4000.0}
    if rates:
        base.update(rates)
    from datetime import UTC, datetime

    ts = datetime(2025, 1, 1, 2, 0, tzinfo=UTC)
    return pd.DataFrame(
        [
            {"base_currency": "USD", "quote_currency": q, "rate": r, "captured_at": ts}
            for q, r in base.items()
        ]
    )


def _snapshots(store_id: str, sku: str, price: float, n: int = 3) -> pd.DataFrame:
    from datetime import UTC, datetime, timedelta

    base_ts = datetime(2025, 1, 1, 3, 0, tzinfo=UTC)
    return pd.DataFrame(
        [
            {
                "store_id": store_id,
                "sku": sku,
                "local_price": price,
                "stock_status": "instock",
                "captured_at": base_ts + timedelta(minutes=i * 15),
            }
            for i in range(n)
        ]
    )


# ---------------------------------------------------------------------------
# detect_drift
# ---------------------------------------------------------------------------


def test_detect_drift_no_drift():
    """Price exactly at canonical → drift_pct close to zero."""
    # CL store, PSI-001, canonical=89 USD, FX=950 CLP → price = 89*950 = 84550 CLP
    snaps = _snapshots("cl", "PSI-001", price=84550.0)
    fx = _fx()
    stores = _stores()

    enriched = normalize_to_usd.fn(snaps, fx, stores)
    drifted = detect_drift.fn(enriched, _products())

    row = drifted[drifted["sku"] == "PSI-001"].iloc[0]
    assert abs(row["drift_pct"]) < 1.0, f"Expected near-zero drift, got {row['drift_pct']:.2f}%"


def test_detect_drift_positive_overpriced():
    """Price 10% above canonical → drift_pct ≈ +10."""
    # canonical=89, FX=950 → fair=84550; +10% → 93005 CLP
    snaps = _snapshots("cl", "PSI-001", price=93005.0)
    fx = _fx()
    enriched = normalize_to_usd.fn(snaps, fx, _stores())
    drifted = detect_drift.fn(enriched, _products())

    row = drifted[drifted["sku"] == "PSI-001"].iloc[0]
    assert 9.0 < row["drift_pct"] < 11.0, f"Expected ~+10% drift, got {row['drift_pct']:.2f}%"


def test_detect_drift_negative_underpriced():
    """Price 20% below canonical → drift_pct ≈ -20."""
    # fair=84550; -20% → 67640 CLP
    snaps = _snapshots("cl", "PSI-001", price=67640.0)
    fx = _fx()
    enriched = normalize_to_usd.fn(snaps, fx, _stores())
    drifted = detect_drift.fn(enriched, _products())

    row = drifted[drifted["sku"] == "PSI-001"].iloc[0]
    assert -21.0 < row["drift_pct"] < -19.0, f"Expected ~-20% drift, got {row['drift_pct']:.2f}%"


def test_detect_drift_empty_snapshots():
    """Empty input produces empty output without errors."""
    empty = pd.DataFrame()
    result = detect_drift.fn(empty, _products())
    assert result.empty


# ---------------------------------------------------------------------------
# classify_severity
# ---------------------------------------------------------------------------


def test_classify_severity_bins():
    """Severity labels follow the defined thresholds."""
    df = pd.DataFrame({"drift_pct": [1.0, -1.0, 7.0, -8.0, 20.0, -16.0]})
    classified = classify_severity.fn(df)
    assert list(classified["severity"]) == [
        "low", "low", "medium", "medium", "critical", "critical"
    ]


def test_classify_severity_boundary_5pct():
    """Exactly 5% triggers 'medium', not 'low'."""
    df = pd.DataFrame({"drift_pct": [5.0, -5.0, 4.99, -4.99]})
    classified = classify_severity.fn(df)
    assert classified["severity"].tolist() == ["medium", "medium", "low", "low"]


def test_classify_severity_boundary_15pct():
    """Exactly 15% triggers 'critical'."""
    df = pd.DataFrame({"drift_pct": [15.0, 14.99]})
    classified = classify_severity.fn(df)
    assert classified["severity"].tolist() == ["critical", "medium"]
