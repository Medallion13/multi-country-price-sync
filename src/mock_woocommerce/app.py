"""FastAPI mock of the WooCommerce REST API.

Exposes ``GET /wp-json/wc/v3/products?store={cl|mx|co}`` returning a
WooCommerce-shaped product list with simulated price noise.

Determinism: clients can pass ``X-Mock-Seed: <int>`` to make a request
deterministic, useful for tests. Without the header, every call yields
fresh noise.
"""

from __future__ import annotations

import random
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query

from mock_woocommerce.data import PRODUCTS, STORES, base_local_price

app = FastAPI(
    title="Mock WooCommerce",
    description="Local mock of the WooCommerce REST API for the price-sync pipelines.",
    version="0.1.0",
)

# Per-request shock probability and magnitudes.
_SHOCK_PROBABILITY = 0.05
_NOISE_SIGMA = 0.02  # gaussian noise on every call
_SHOCK_MIN = 0.10  # 10% lower bound for shock magnitude
_SHOCK_MAX = 0.25  # 25% upper bound for shock magnitude

StoreId = Literal["cl", "mx", "co"]


def _quantize(value: Decimal) -> Decimal:
    """Round to two decimal places, half-up (matches WooCommerce display)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _noisy_price(base: Decimal, rng: random.Random) -> Decimal:
    """Apply gaussian noise plus a small chance of a directional shock."""
    noise = rng.gauss(0.0, _NOISE_SIGMA)
    if rng.random() < _SHOCK_PROBABILITY:
        magnitude = rng.uniform(_SHOCK_MIN, _SHOCK_MAX)
        sign = 1 if rng.random() < 0.5 else -1
        noise += sign * magnitude
    factor = Decimal(1) + Decimal(str(noise))
    return _quantize(base * factor)


def _build_rng(seed_header: str | None) -> random.Random:
    """Deterministic RNG when X-Mock-Seed is provided, fresh otherwise."""
    if seed_header is None:
        return random.Random()
    try:
        return random.Random(int(seed_header))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="X-Mock-Seed must be an integer") from exc


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/wp-json/wc/v3/products")
async def list_products(
    store: Annotated[StoreId, Query(description="Store identifier")],
    x_mock_seed: Annotated[str | None, Header(alias="X-Mock-Seed")] = None,
) -> list[dict[str, str | int]]:
    """Return the product catalog for the given store, with noisy local prices.

    Response shape mimics the relevant subset of the WooCommerce REST API v3:
    ``id``, ``sku``, ``name``, ``regular_price``, ``price``, ``stock_status``.
    """
    if not any(s.store_id == store for s in STORES):
        raise HTTPException(status_code=404, detail=f"Unknown store: {store}")

    rng = _build_rng(x_mock_seed)

    products: list[dict[str, str | int]] = []
    for idx, product in enumerate(PRODUCTS, start=1):
        base = base_local_price(product.sku, store)
        price = _noisy_price(base, rng)
        products.append(
            {
                "id": idx,
                "sku": product.sku,
                "name": product.name,
                "regular_price": str(price),
                "price": str(price),
                "stock_status": "instock",
            }
        )
    return products
