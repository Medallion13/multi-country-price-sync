"""Pydantic schemas for cross-task data exchange

internal pipeline data crosses task boundaries as Pydantic models so Prefect's INPUTS
cache policy can has inputs deterministically and runtime errors
surface at parse time, not deep inside SQL writes
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WooCommerceProduct(BaseModel):
    """Subset of the WooCommerce product payload the pipeline consumes."""

    model_config = ConfigDict(extra="ignore")

    sku: str
    name: str
    price: Decimal
    stock_status: str = "instock"

    @field_validator("price", mode="before")
    @classmethod
    def _coerce_price(cls, v: object) -> Decimal:
        # WooCommerce returns price as a string; pandas/json may yield float.
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class StorePriceCapture(BaseModel):
    """A single (Store, sku) price observation at a point in time"""

    store_id: str
    sku: str
    local_price: Decimal
    stock_status: str
    captured_at: datetime


class StoreFetchResult(BaseModel):
    """Outcome of one store fetch, including health metadata."""

    store_id: str
    captures: list[StorePriceCapture] = Field(default_factory=list)
    status_code: int
    latency_ms: int
    captured_at: datetime
    error: str | None = None


class FXRateCapture(BaseModel):
    """A single (base, quote) exchange rate observation."""

    base_currency: str
    quote_currency: str
    rate: Decimal
    captured_at: datetime


class FXFetchResult(BaseModel):
    """Outcome of one FX fetch, including health metadata."""

    rates: list[FXRateCapture] = Field(default_factory=list)
    status_code: int
    latency_ms: int
    captured_at: datetime
    error: str | None = None


class PriceChangeEvent(BaseModel):
    """A detected price movement above the configured threshold."""

    store_id: str
    sku: str
    prev_price: Decimal
    new_price: Decimal
    pct_change: Decimal
    detected_at: datetime
