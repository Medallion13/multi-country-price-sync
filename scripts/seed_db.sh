#!/usr/bin/env bash
# Seed catalog tables (products, stores) from the mock data module.
# Idempotent: safe to run multiple times.
#
# Run from the repo root:
#   ./scripts/seed_db.sh

set -euo pipefail

# Load .env into the shell environment.
set -a
# shellcheck disable=SC1091
source .env
set +a

uv run python -m mock_woocommerce.seed
