"""SQLAlchemy ORM models for the application schema.

All tables live in the ``app`` schema. The ``public`` schema is reserved
for Prefect's internal tables and Alembic's version table also lives in
``app`` to keep ``public`` exclusively for the orchestrator.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

APP_SCHEMA = "app"


class Base(DeclarativeBase):
    """Declarative base. Every table defaults to the app schema."""

    metadata = MetaData(schema=APP_SCHEMA)


# Catalog


class Product(Base):
    """Canonical product catalog. Source of truth for canonical USD base price"""

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Store(Base):
    """Per-country WooCommerce store metadata."""

    __tablename__ = "stores"

    store_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


# RAW data - light pipeline territory


class PriceSnapshot(Base):
    """Raw price reading per (store, sku) bucketed to the minute.

    The minute bucket is what makes the light pipeline idempotent: re-running
    within the same minute hits ``ON CONFLICT DO NOTHING``.
    """

    __tablename__ = "price_snapshots"

    store_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey(f"{APP_SCHEMA}.stores.store_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{APP_SCHEMA}.products.sku", ondelete="RESTRICT"),
        nullable=False,
    )
    local_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    stock_status: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    captured_minute: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        Computed(
            "date_trunc('minute', captured_at AT TIME ZONE 'UTC')",
            persisted=True,
        ),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("store_id", "sku", "captured_minute", name="pk_price_snapshots"),
        Index("ix_price_snapshots_captured_at", "captured_at"),
    )


class FxRate(Base):
    """FX rate snapshot bucketed to the minute, same idempotency strategy."""

    __tablename__ = "fx_rates"

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    captured_minute: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        Computed(
            "date_trunc('minute', captured_at AT TIME ZONE 'UTC')",
            persisted=True,
        ),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "base_currency",
            "quote_currency",
            "captured_minute",
            name="pk_fx_rates",
        ),
        Index("ix_fx_rates_captured_at", "captured_at"),
    )


class PriceChangeEvent(Base):
    """Emitted by the light pipeline when local price moves more than 5%."""

    __tablename__ = "price_change_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey(f"{APP_SCHEMA}.stores.store_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{APP_SCHEMA}.products.sku", ondelete="RESTRICT"),
        nullable=False,
    )
    prev_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    new_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    pct_change: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("store_id", "sku", "detected_at", name="uq_price_change_events"),
        Index("ix_price_change_events_detected_at", "detected_at"),
    )


class ApiHealthLog(Base):
    """Per-call observability for upstream HTTP endpoints."""

    __tablename__ = "api_health_log"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey(f"{APP_SCHEMA}.stores.store_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_api_health_log_captured_at", "captured_at"),)


# Processed dat - Heavy pipeline territory
class DailyPriceReport(Base):
    """Aggregated daily report. One row per ``report_date``."""

    __tablename__ = "daily_price_reports"

    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    products_analyzed: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_drift_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    alerts_critical: Mapped[int] = mapped_column(Integer, nullable=False)
    alerts_medium: Mapped[int] = mapped_column(Integer, nullable=False)
    alerts_low: Mapped[int] = mapped_column(Integer, nullable=False)
    fx_rate_usd_clp_avg: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    fx_rate_usd_mxn_avg: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    fx_rate_usd_cop_avg: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PriceAlert(Base):
    """One alert per (report_date, store, sku). UPSERTed by the heavy flow."""

    __tablename__ = "price_alerts"

    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    store_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey(f"{APP_SCHEMA}.stores.store_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{APP_SCHEMA}.products.sku", ondelete="RESTRICT"),
        nullable=False,
    )
    avg_local_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    avg_price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    canonical_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    drift_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    cause_hint: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("report_date", "store_id", "sku", name="pk_price_alerts"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'critical')",
            name="ck_price_alerts_severity",
        ),
        CheckConstraint(
            "cause_hint IN ('fx_movement', 'manual_change', 'mixed', 'unknown')",
            name="ck_price_alerts_cause_hint",
        ),
        Index("ix_price_alerts_report_date", "report_date"),
    )
