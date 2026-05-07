# multi-country-price-sync

Pipeline de sincronización de precios multi-país (Chile, México, Colombia) con detección de drift y alertas clasificadas por severidad.

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│  docker-compose (local)                                             │
│                                                                     │
│  ┌──────────────┐    ┌─────────────────┐    ┌──────────────────┐   │
│  │   postgres   │◄───│  prefect-server  │    │ mock-woocommerce  │   │
│  │ (schema app) │    │  API + UI :4200  │    │   FastAPI :8000   │   │
│  └──────┬───────┘    └────────┬────────┘    └────────┬─────────┘   │
│         │                     │                       │              │
│  ┌──────┴──────────┐  ┌───────┴───────────┐           │              │
│  │ worker-light    │  │  worker-heavy      │           │              │
│  │ (Process pool)  │  │  (Docker pool)     │◄──────────┘              │
│  │  cron */15 *    │  │  cron 0 3 * * *    │                          │
│  └────────┬────────┘  └───────┬────────────┘                         │
│           │                   │ spawns on-demand                      │
│           │                   ▼                                       │
│           │          ┌────────────────┐                               │
│           └─────────►│ heavy:latest   │ (ephemeral container)         │
│    writes raw         └───────┬────────┘                              │
│                               │ writes processed                      │
└───────────────────────────────┴───────────────────────────────────────┘

Raw tables (light):    price_snapshots · fx_rates · price_change_events · api_health_log
Processed (heavy):     daily_price_reports · price_alerts
```

---

## Pipelines

### Liviano — cada 15 minutos

```
fetch_fx_rates ──────────────────────────────────────────────────────┐
fetch_store_prices(cl) ─┐                                             │
fetch_store_prices(mx) ─┼─► quick_drift_check ─► upsert_change_events │
fetch_store_prices(co) ─┘         │                                   │
                         upsert_raw_snapshots                          │
                         upsert_raw_fx ◄───────────────────────────────┘
                         validate_response_health
```

- **Work pool:** Process (`prefect-worker-light` en compose)
- **Caché:** `cache_policy=INPUTS`, `cache_expiration=14 min` en tareas de fetch
- **Idempotencia:** `ON CONFLICT (store_id, sku, captured_minute) DO NOTHING`

### Pesado — diario 03:00 UTC

```
load_day_window → normalize_to_usd → detect_drift → classify_severity
               → aggregate_report  → write_outputs
```

- **Work pool:** Docker (`prefect-worker-heavy` en compose, imagen `pricesync/heavy:latest`)
- **Aislamiento:** contenedor efímero por run — pandas corre fuera del proceso principal
- **Idempotencia:** `ON CONFLICT (report_date) DO UPDATE` en ambas tablas processed

---

## Decisiones técnicas

| Decisión | Rationale |
|---|---|
| psycopg3 sync para escrituras | Stack simple sin asyncpg — las tareas de DB son breves y el pool es suficiente |
| httpx async para fetches | Fan-out paralelo a 3 stores + FX sin bloquear el event loop |
| `INPUTS` cache policy | Re-trigger dentro del mismo minuto devuelve resultado cacheado sin trabajo redundante |
| `len(rows)` en lugar de `rowcount` | Bug conocido psycopg3 + executemany: rowcount reporta filas procesadas, no insertadas |
| pandas en imagen separada | Imagen base <200 MB liviana; pandas solo en el worker heavy |
| `cause_hint = 'unknown'` | `infer_cause_hint` recortado por política de tiempo (plan §13) |
| FX: open.er-api.com/v6 sin key | Tier gratuito suficiente para la prueba |

---

## Idempotencia — tres capas

1. **Caché Prefect** (`INPUTS`, TTL 14 min): re-ejecución del liviano dentro de la misma ventana retorna resultado cacheado sin HTTP ni escrituras.
2. **Restricciones de DB**:
   - Raw: `INSERT … ON CONFLICT (store_id, sku, captured_minute) DO NOTHING`
   - Processed: `INSERT … ON CONFLICT (report_date, …) DO UPDATE`
3. *(Recortado por tiempo)* `cleanup_old_raw` — ver segunda iteración.

---

## Cómo levantarlo

### Prerequisitos

- Docker + Docker Compose plugin
- `uv` instalado
- Puertos 4200 y 5432 libres

### 1. Variables de entorno

```bash
cp .env.example .env
```

### 2. Infraestructura base

```bash
docker compose up -d postgres prefect-server mock-woocommerce prefect-worker-light
```

### 3. Migraciones y seed

```bash
uv sync
uv run alembic upgrade head
uv run python -m src.mock_woocommerce.seed
```

### 4. Registrar el liviano

```bash
PREFECT_API_URL=http://localhost:4200/api \
  uv run prefect work-pool create light-pool --type process

PREFECT_API_URL=http://localhost:4200/api \
  uv run python -m src.deploy.deploy_light
```

### 5. Pipeline pesado (Docker work pool)

```bash
# Construir la imagen heavy (pandas incluido)
docker build -t pricesync/heavy:latest -f docker/heavy.Dockerfile .

# Crear el work pool Docker (una sola vez)
PREFECT_API_URL=http://localhost:4200/api \
  uv run prefect work-pool create heavy-pool --type docker

# Levantar el worker heavy
docker compose up -d prefect-worker-heavy

# Registrar el deployment
PREFECT_API_URL=http://localhost:4200/api \
  uv run python -m src.deploy.deploy_heavy

# Trigger manual (por defecto analiza ayer; se puede pasar report_date)
PREFECT_API_URL=http://localhost:4200/api \
  uv run prefect deployment run 'heavy-price-analysis/heavy-price-analysis'
```

### 6. Verificar datos

```sql
SELECT store_id, COUNT(*), MAX(captured_at) FROM app.price_snapshots GROUP BY 1;
SELECT quote_currency, AVG(rate)            FROM app.fx_rates GROUP BY 1;
SELECT * FROM app.daily_price_reports ORDER BY report_date DESC LIMIT 5;
SELECT store_id, sku, drift_pct, severity   FROM app.price_alerts
  ORDER BY abs(drift_pct) DESC LIMIT 20;
```

---

## Tests

```bash
uv run pytest tests/unit/          # sin DB, siempre disponibles
uv run pytest tests/integration/   # requieren Postgres (skip automático si no está)
uv run pytest                      # todos
```

---

## Entrega local — declaración honesta

La prueba especifica despliegue en VM Hetzner con HTTPS + basic auth vía Caddy. **No se realizó el deploy en VM por restricción de tiempo** (quedan <30 min al momento de esta entrega).

Lo que está listo para VM:
- `caddy/Caddyfile` con reverse proxy y basic auth
- `docker-compose.prod.yml` con override de Caddy
- `scripts/deploy_vm.sh` con los pasos completos

El sistema corre localmente y se verifica manualmente. El screencast muestra la UI local, runs en Prefect, y datos en Postgres.

---

## Segunda iteración

1. **`infer_cause_hint`**: cruzar `price_change_events` con movimiento FX del día para clasificar causa probable (cambio manual vs movimiento cambiario).
2. **`cleanup_old_raw`**: eliminar snapshots con `captured_at < NOW() - 30 days` al final del pesado.
3. **Deploy VM**: Hetzner CX23 + Caddy + DuckDNS usando `docker-compose.prod.yml` y `scripts/deploy_vm.sh`.
4. **CI GitHub Actions**: lint + format + `pytest tests/unit/` en cada push a main.
5. **Fix typo MXN**: `"MXM"` → `"MXN"` en `QUOTE_CURRENCIES` del liviano para que los rates de México se persistan.
6. **Tests adicionales**: `test_severity_classifier.py`, `test_idempotency_heavy.py`, `test_change_detection.py`.
7. **Structured logging**: JSON formatter en producción con campos `flow`, `task`, `store_id`, `run_id`.

---

## Estructura del repo

```
src/
├── pipelines/
│   ├── light/   flow.py + tasks.py   (fetch, drift check, upsert raw)
│   └── heavy/   flow.py + tasks.py   (normalise, drift, classify, report)
├── shared/      config, db, http_clients, models, schemas
├── mock_woocommerce/  FastAPI mock con seed y ruido simulado
└── deploy/      deploy_light.py + deploy_heavy.py

docker/
├── prefect-base.Dockerfile   base liviana
├── heavy.Dockerfile          añade pandas + copia src/
└── mock-woocommerce.Dockerfile

migrations/versions/0001_initial_schema.py
tests/
├── unit/        test_drift_detection.py
└── integration/ test_idempotency_light.py
```
