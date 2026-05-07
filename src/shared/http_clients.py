"""Async HTTP client factories for external services.

Both clients are async because the light pipeline fans out store fetches
with ``asyncio.gather``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from src.shared.config import get_settings


@asynccontextmanager
async def woocommerce_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client pointed at the mock woocommerce base URL."""
    settings = get_settings()
    async with httpx.AsyncClient(
        base_url=settings.mock_woocommerce_base,
        timeout=settings.http_timeout_s,
        headers={"Accept": "application/json"},
    ) as client:
        yield client


@asynccontextmanager
async def fx_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client pointed at the FX provider base URL."""
    settings = get_settings()
    async with httpx.AsyncClient(
        base_url=settings.fx_api_base,
        timeout=settings.http_timeout_s,
        headers={"Accept": "application/json"},
    ) as client:
        yield client
