# Multi-Country Price Sync

Backend system that captures product prices across multi-country e-commerce
stores (CL, MX, CO), applies live FX rates, detects drift against canonical
USD prices, and emits classified alerts.

**Stack:** Python 3.12 · Prefect 3 · Postgres 16 · SQLAlchemy + Alembic ·
FastAPI · httpx · Docker Compose · Caddy.

## Status

Work in progress.

## Quick start (local)

```bash
cp .env.example .env
docker compose up -d
# Prefect UI: http://localhost:4200
```