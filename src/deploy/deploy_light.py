"""Register the light pipeline deployment against the Prefect server.

The worker process running in docker-compose picks up runs from the
``light-pool`` work pool. This script only creates/updates the deployment
metadata; it does not execute flows.

Run from the host:

    uv run python -m src.deploy.deploy_light
"""

from __future__ import annotations

from pathlib import Path

from prefect import flow

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    # ``flow.from_source`` is sync_compatible: the static type is a Coroutine,
    # but at runtime in a sync context it returns the Flow directly. The
    # ignore is the documented escape hatch for this Prefect design choice.
    flow.from_source(
        source=str(REPO_ROOT),
        entrypoint="src/pipelines/light/flow.py:light_price_sync",
    ).deploy(  # type: ignore[union-attr]
        name="light-price-sync",
        work_pool_name="light-pool",
        cron="*/15 * * * *",
        tags=["light", "prices"],
        description="Capture store prices and FX rates every 15 minutes.",
    )


if __name__ == "__main__":
    main()
