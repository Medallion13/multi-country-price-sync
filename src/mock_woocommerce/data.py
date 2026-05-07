"""Seed data shared by the mock API and the database seeder.

Single source of truth for products, stores, baseline FX rates and the
per-store structural multipliers used to simulate persistent drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ProductSeed:
    sku: str
    name: str
    canonical_price_usd: Decimal


@dataclass(frozen=True, slots=True)
class StoreSeed:
    store_id: str
    country: str
    currency: str
    base_url: str


# Products
PRODUCTS: tuple[ProductSeed, ...] = (
    ProductSeed("PSI-001", "Introducción a la Psicología Clínica", Decimal("89.00")),
    ProductSeed("PSI-002", "Terapia Cognitivo-Conductual Avanzada", Decimal("149.00")),
    ProductSeed("PSI-003", "Psicología Infantil y del Adolescente", Decimal("119.00")),
    ProductSeed("PSI-004", "Neuropsicología Aplicada", Decimal("179.00")),
    ProductSeed("PSI-005", "Mindfulness y Reducción del Estrés", Decimal("59.00")),
    ProductSeed("PSI-006", "Evaluación e Informes Psicológicos", Decimal("99.00")),
    ProductSeed("PSI-007", "Introducción a la Psicología Forense", Decimal("139.00")),
    ProductSeed("PSI-008", "Trauma y Procesos de Recuperación", Decimal("109.00")),
)

# Stores
STORES: tuple[StoreSeed, ...] = (
    StoreSeed("cl", "CL", "CLP", "http://mock-woocommerce:8000"),
    StoreSeed("mx", "MX", "MXN", "http://mock-woocommerce:8000"),
    StoreSeed("co", "CO", "COP", "http://mock-woocommerce:8000"),
)

# FX baseline (USD -> local currency).
FX_BASE: dict[str, Decimal] = {
    "CLP": Decimal("950"),
    "MXN": Decimal("18"),
    "COP": Decimal("4000"),
}
# Per-store structural multiplier applied on top of canonical_usd * fx.
# Simulates persistent pricing decisions per country: MX is intentionally
# overpriced, CO underpriced, CL aligned. The heavy pipeline should detect
# this as a stable drift, not as noise.
STORE_MULTIPLIERS: dict[str, Decimal] = {
    "cl": Decimal("1.00"),
    "mx": Decimal("1.05"),
    "co": Decimal("0.95"),
}


def base_local_price(sku: str, store_id: str) -> Decimal:
    """Return the deterministic local price for a (sku, store) pair.

    This is the price *before* per-request noise. Used both by the mock
    (as the center of the noise distribution) and by tests that need a
    predictable reference point.
    """
    product = next(p for p in PRODUCTS if p.sku == sku)
    store = next(s for s in STORES if s.store_id == store_id)
    fx = FX_BASE[store.currency]
    multiplier = STORE_MULTIPLIERS[store_id]
    return product.canonical_price_usd * fx * multiplier
