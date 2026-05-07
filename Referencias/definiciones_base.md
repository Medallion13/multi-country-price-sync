# Definiciones Base del Proyecto

Documento sintético con las decisiones cerradas para la prueba técnica de backend. Fuente única de verdad para el `project` de Claude y para la implementación.

---

## 1. Problema

Sistema de sincronización de precios de productos educativos vendidos en stores multi-país (Chile, México, Colombia). Captura precios locales y tipos de cambio frecuentemente, consolida diariamente, detecta drift contra precios canónicos en USD y produce alertas clasificadas por severidad y causa probable (movimiento FX vs cambio manual).

---

## 2. Stack técnico

| Capa | Tecnología | Versión |
|---|---|---|
| Lenguaje | Python | 3.12 |
| Package manager | uv | latest |
| Orquestador | Prefect | 3.6.29 |
| DB | Postgres | 16 |
| ORM + migraciones | SQLAlchemy + Alembic | 2.0 + 1.13 |
| DB driver | psycopg[binary] | 3.2 |
| Cliente HTTP | httpx | 0.28 |
| Mock API | FastAPI + uvicorn | 0.115 + 0.32 |
| Config | pydantic + pydantic-settings | 2.10 + 2.7 |
| Procesamiento pesado | pandas | 2.2 |
| Tests | pytest + pytest-asyncio | 8.3 + 0.24 |
| Lint + format | ruff | 0.9 |
| Reverse proxy | Caddy | 2.x |
| Containers | Docker + Compose | latest |

Versiones a verificar contra PyPI al inicializar el proyecto.

---

## 3. Arquitectura de despliegue

### 3.1 Infraestructura

- VM **Hetzner CX23** (2 vCPU Intel, 4 GB RAM, 40 GB SSD NVMe), Ubuntu 24.04 LTS.
- Región Nuremberg (DE) o Falkenstein (DE).
- DNS dinámico vía DuckDNS.
- HTTPS automático vía Caddy + Let's Encrypt.
- Basic auth en Caddy para proteger la UI de Prefect.

### 3.2 Servicios en docker-compose

```
postgres                 → DB única, schemas: prefect, app
prefect-server           → API + UI en :4200 (interno)
prefect-worker-light     → Process work pool
prefect-worker-heavy     → Docker work pool, spawn de heavy:latest
mock-woocommerce         → FastAPI con endpoints CL/MX/CO
caddy                    → :443 público, reverse proxy a prefect-server
```

### 3.3 Imágenes Docker

- `prefect-base.Dockerfile` → base con prefect-client + libs livianas. Usada por server y workers.
- `heavy.Dockerfile` → con pandas y dependencias pesadas. Construida por compose, ejecutada on-demand por el Docker work pool.
- `mock-woocommerce.Dockerfile` → FastAPI mock.

---

## 4. Pipelines

### 4.1 Pipeline liviano

- **Frecuencia:** cron `*/15 * * * *`.
- **Work pool:** Process.
- **Duración objetivo:** 5–15 segundos.
- **Tasks:**
  1. `fetch_fx_rates` — llamada a exchangerate.host (USD → CLP, MXN, COP). Cacheada por minuto.
  2. `fetch_store_prices` — llamadas paralelas a los 3 stores del mock. Cacheadas por minuto.
  3. `validate_response_health` — registra latencia y status code por store.
  4. `quick_drift_check` — compara precio actual contra última lectura del mismo SKU; si cambió >5% emite evento.
  5. `upsert_raw_tables` — persiste snapshots, fx_rates, change_events, health_log.
- **Caché:** `cache_policy=INPUTS`, `cache_expiration=14 minutes`.
- **Concurrencia:** ilimitada (la caché evita trabajo redundante).

### 4.2 Pipeline pesado

- **Frecuencia:** cron `0 3 * * *`.
- **Work pool:** Docker, imagen `heavy:latest`.
- **Duración objetivo:** 30–60 segundos.
- **Tasks:**
  1. `load_day_window` — carga snapshots y fx_rates del día anterior con pandas.
  2. `normalize_to_usd` — aplica FX vigente al momento del snapshot, columna `local_price_usd`.
  3. `detect_drift` — calcula `drift_pct` contra `canonical_price_usd`.
  4. `classify_severity` — bins low/medium/critical por umbral.
  5. `infer_cause_hint` — cruza con change_events y movimiento FX para clasificar causa probable.
  6. `aggregate_report` — estadísticas agregadas por país y globales.
  7. `write_outputs` — UPSERT en `daily_price_reports` y `price_alerts`.
  8. `cleanup_old_raw` — elimina snapshots con más de 30 días. Configurable, desactivable.

### 4.3 Encadenamiento

Acoplamiento por DB. El pesado lee tablas raw filtrando por `date(captured_at) = yesterday`. No hay trigger directo entre flows.

---

## 5. Modelo de datos

### 5.1 Schema `app`

**Catálogo:**

```sql
products (sku PK, name, canonical_price_usd, is_active, created_at, updated_at)
stores (store_id PK, country, currency, base_url, is_active)
```

**Raw — pobladas por el liviano:**

```sql
price_snapshots (
    store_id, sku, local_price, stock_status,
    captured_at, captured_minute GENERATED,
    PRIMARY KEY (store_id, sku, captured_minute)
)

fx_rates (
    base_currency, quote_currency, rate,
    captured_at, captured_minute GENERATED,
    PRIMARY KEY (base_currency, quote_currency, captured_minute)
)

price_change_events (
    event_id BIGSERIAL PK,
    store_id, sku, prev_price, new_price, pct_change, detected_at,
    UNIQUE (store_id, sku, detected_at)
)

api_health_log (store_id, status_code, latency_ms, captured_at)
```

**Processed — pobladas por el pesado:**

```sql
daily_price_reports (
    report_date PK, products_analyzed, avg_drift_pct,
    alerts_critical, alerts_medium, alerts_low,
    fx_rate_usd_clp_avg, fx_rate_usd_mxn_avg, fx_rate_usd_cop_avg,
    generated_at
)

price_alerts (
    report_date, store_id, sku,
    avg_local_price, avg_price_usd, canonical_usd, drift_pct,
    severity, cause_hint,
    PRIMARY KEY (report_date, store_id, sku)
)
```

### 5.2 Migraciones

Gestionadas con Alembic. Migración inicial `0001_initial_schema.py` crea schema `app`, todas las tablas y carga seed mínimo de `products` y `stores`.

---

## 6. Idempotencia

Tres capas independientes:

1. **Caché de Prefect** en tasks costosas del liviano. Re-ejecución dentro de la misma ventana devuelve resultado cacheado sin trabajo extra.
2. **Restricciones de DB**:
   - Raw: `INSERT ... ON CONFLICT (...) DO NOTHING`.
   - Processed: `INSERT ... ON CONFLICT (...) DO UPDATE`.
3. **Retención** ejecutada por `cleanup_old_raw` al final del pesado: elimina raw con más de 30 días.

---

## 7. Configuración y secretos

- Variables de entorno gestionadas con `pydantic-settings`.
- `.env.example` versionado con valores de desarrollo.
- `.env` y `.env.production` ignorados en `.gitignore`.
- En la VM, `.env.production` cargado por compose.
- Variables clave: `POSTGRES_*`, `PREFECT_API_URL`, `EXCHANGERATE_API_BASE`, `MOCK_WOOCOMMERCE_BASE`, `BASIC_AUTH_USER`, `BASIC_AUTH_PASS_HASH`.

---

## 8. Logging y observabilidad

- Prefect UI como interfaz primaria de observabilidad.
- `logging` estándar de Python con formato JSON line en producción y texto en local.
- Tablas `price_change_events` y `api_health_log` como log estructurado de eventos de negocio.

---

## 9. Tests

```
tests/
├── unit/
│   ├── test_drift_detection.py
│   ├── test_severity_classifier.py
│   └── test_currency_normalizer.py
└── integration/
    ├── test_idempotency_light.py
    ├── test_idempotency_heavy.py
    └── test_change_detection.py
```

- Cobertura objetivo: 60–70% en módulos de lógica de negocio.
- Integration tests usan Postgres efímero vía Docker.
- Comando: `uv run pytest`.

---

## 10. CI

GitHub Actions, workflow único `.github/workflows/ci.yml`:

- Trigger: `push` y `pull_request` a `main`.
- Steps: setup uv → `uv sync --frozen` → `ruff check` → `ruff format --check` → `pytest tests/unit/`.

Bloque opcional. Primero en recortar si el reloj aprieta.

---

## 11. Estructura del repo

```
.
├── README.md
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
│
├── docker/
│   ├── prefect-base.Dockerfile
│   ├── heavy.Dockerfile
│   └── mock-woocommerce.Dockerfile
│
├── caddy/
│   └── Caddyfile
│
├── src/
│   ├── pipelines/
│   │   ├── light/
│   │   │   ├── flow.py
│   │   │   └── tasks.py
│   │   └── heavy/
│   │       ├── flow.py
│   │       └── tasks.py
│   ├── shared/
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── http_clients.py
│   │   ├── models.py
│   │   └── schemas.py
│   ├── mock_woocommerce/
│   │   ├── app.py
│   │   └── data.py
│   └── deploy/
│       ├── deploy_light.py
│       └── deploy_heavy.py
│
├── migrations/
│   ├── alembic.ini
│   └── versions/
│       └── 0001_initial_schema.py
│
├── scripts/
│   ├── bootstrap.sh
│   ├── seed_db.sh
│   └── deploy_vm.sh
│
├── reports/
│   └── queries.sql
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
└── .github/
    └── workflows/
        └── ci.yml
```

---

## 12. Entrega

### 12.1 Artefactos

1. Repositorio público en GitHub.
2. URL pública de la UI de Prefect protegida con basic auth.
3. Screencast de 2–3 minutos.
4. README completo.
5. PR description con resumen sintético.

### 12.2 Guion del screencast

- 0:00–0:15 — Apertura.
- 0:15–0:45 — UI de Prefect en VM, deployments y runs.
- 0:45–1:30 — Trigger manual del liviano, task graph en vivo, mostrar caché.
- 1:30–2:15 — Postgres con queries que muestran raw y processed.
- 2:15–2:45 — Trigger del pesado, `docker ps` mostrando contenedor efímero.
- 2:45–3:00 — Cierre.

### 12.3 Convenciones de commits

Conventional Commits con scope: `feat(light):`, `feat(heavy):`, `fix(...)`, `test(...)`, `docs:`, `chore(...)`, `ci:`.

### 12.4 Estructura de la PR description

Resumen, problema elegido, pipelines, decisiones clave, cómo verificar, trade-offs.

---

## 13. Decisiones diferidas al inicio del trabajo

- **CI:** se incluye si quedan ≥20 min al final del bloque de trabajo, se omite en caso contrario.
