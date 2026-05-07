"""Register the heavy pipeline deployment against the Prefect server.

The prefect-worker-heavy container (Docker work pool) picks up runs and
spawns a fresh ``pricesync/heavy:latest`` container for each run.

Prerequisites:
    1. Build the heavy image:
       docker build -t pricesync/heavy:latest -f docker/heavy.Dockerfile .
    2. Create the Docker work pool (one-time):
       prefect work-pool create heavy-pool --type docker
    3. Run this script:
       uv run python -m src.deploy.deploy_heavy

Environment variables (read from .env or shell):
    COMPOSE_NETWORK  Docker network where prefect-server and postgres are
                     reachable by service name.
                     Default: multi-country-price-sync_default
    POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB  forwarded to the
                     spawned container as env vars.
"""

from __future__ import annotations

import os
from pathlib import Path

from prefect import flow

REPO_ROOT = Path(__file__).resolve().parents[2]

COMPOSE_NETWORK = os.getenv("COMPOSE_NETWORK", "multi-country-price-sync_default")
POSTGRES_USER = os.getenv("POSTGRES_USER", "pipelines")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "pipelines")
POSTGRES_DB = os.getenv("POSTGRES_DB", "pipelines")
FX_API_BASE = os.getenv("FX_API_BASE", "https://open.er-api.com/v6")


def main() -> None:
    flow.from_source(
        source=str(REPO_ROOT),
        entrypoint="src/pipelines/heavy/flow.py:heavy_price_analysis",
    ).deploy(  # type: ignore[union-attr]
        name="heavy-price-analysis",
        work_pool_name="heavy-pool",
        cron="0 3 * * *",
        tags=["heavy", "analysis"],
        description="Daily price drift analysis, severity classification, and alerting.",
        image="pricesync/heavy:latest",
        push=False,
        build=False,
        job_variables={
            "image_pull_policy": "Never",
            "networks": [COMPOSE_NETWORK],
            "env": {
                "PREFECT_API_URL": "http://prefect-server:4200/api",
                "PREFECT_RESULTS_PERSIST_BY_DEFAULT": "true",
                "POSTGRES_HOST": "postgres",
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": POSTGRES_DB,
                "POSTGRES_USER": POSTGRES_USER,
                "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
                "FX_API_BASE": FX_API_BASE,
                "MOCK_WOOCOMMERCE_BASE": "http://mock-woocommerce:8000",
                "PYTHONPATH": "/app",
            },
        },
    )


if __name__ == "__main__":
    main()
