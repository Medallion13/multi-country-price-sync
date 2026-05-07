# Plan de Trabajo — 6 horas

Plan ejecutable cronometrado para implementar la prueba técnica. Cada bloque tiene objetivo, tareas, deliverable verificable y criterio de corte.

---

## Pre-trabajo (antes de arrancar el cronómetro)

Hacer **antes** de iniciar las 6 horas. No cuenta dentro del plan.

- Cuenta DuckDNS lista, subdominio reservado y token guardado.
- Cuenta Hetzner Cloud lista, método de pago verificado, identidad confirmada.
- Editor configurado con extensión de Prefect / Python lista.
- Cliente de DB (psql, pgcli, DBeaver) instalado.
- Software de grabación de pantalla probado.

---

## Hora 1 — Bootstrap (0:00 – 1:00)

**Objetivo:** repo creado, estructura base, Prefect UI corriendo en local.

### 0:00 – 0:10 — Provisión de la VM
- Crear servidor Hetzner CX23, Ubuntu 24.04, 4 GB RAM, en Nuremberg.
- Anotar IP pública.
- Lanzar provisión y **continuar trabajando** mientras la VM se levanta.

### 0:10 – 0:25 — Repo y estructura base
- Crear repo en GitHub.
- Clonar local.
- `uv init`, configurar `pyproject.toml` con dependencias del documento de definiciones.
- Crear estructura de carpetas vacías según definiciones base.
- `.gitignore`, `.env.example`, README mínimo.
- Configurar `ruff` en `pyproject.toml`.
- Primer commit: `chore: initial project structure`.

### 0:25 – 0:50 — docker-compose mínimo funcional
- Escribir `docker-compose.yml` con servicios: `postgres`, `prefect-server`.
- `docker/prefect-base.Dockerfile` con Python 3.12 + uv + prefect-client.
- Variables de entorno desde `.env`.
- `docker compose up -d`.
- **Verificar:** `http://localhost:4200` carga UI de Prefect.

### 0:50 – 1:00 — Configurar VM con acceso SSH
- Conectar por SSH a la VM ya provisionada.
- Instalar Docker + Docker Compose plugin.
- Crear usuario no-root con permisos docker.
- Configurar firewall de Hetzner Cloud: puertos 22, 80, 443.
- Commit del compose: `feat(infra): add base docker-compose with postgres and prefect-server`.

**Deliverable de la hora 1:** repo en GitHub, compose funciona local, VM accesible por SSH.
**Si te atrasas:** continúa al bloque siguiente, recupera en el buffer de la hora 6.

---

## Hora 2 — Schema y Mock (1:00 – 2:00)

**Objetivo:** Postgres con schema completo, mock-woocommerce respondiendo.

### 1:00 – 1:25 — Schema + Alembic
- Configurar `alembic.ini`, `migrations/env.py`.
- Definir modelos SQLAlchemy en `src/shared/models.py` para todas las tablas del schema `app`.
- Generar primera migración: `alembic revision --autogenerate -m "initial schema"`.
- Ajustar manualmente la migración para crear el schema `app` antes de las tablas.
- `alembic upgrade head` contra Postgres del compose.
- **Verificar:** `\dt app.*` en psql muestra todas las tablas.

### 1:25 – 1:55 — Mock WooCommerce + seed
- `src/mock_woocommerce/data.py` con ~10 productos, 3 stores, precios coherentes con `canonical_price_usd` y FX aproximado.
- `src/mock_woocommerce/app.py` con FastAPI: endpoint `GET /wp-json/wc/v3/products` que acepta header o path para identificar store.
- Drift simulado: cada llamada agrega ruido ±2% al precio base, y con baja probabilidad un cambio mayor.
- `docker/mock-woocommerce.Dockerfile`.
- Agregar servicio al compose.
- Script `scripts/seed_db.sh` que carga `products` y `stores` con valores fijos.
- **Verificar:** `curl http://localhost:8000/wp-json/wc/v3/products?store=cl` devuelve JSON válido.

### 1:55 – 2:00 — Commit y check
- `feat(db): add alembic migrations and initial schema`.
- `feat(mock): add fastapi woocommerce mock with seed data`.

**Deliverable hora 2:** DB con schema completo, mock respondiendo en los 3 stores, productos seed cargados.
**Si te atrasas:** simplifica seed a 5 productos. No reduzcas tablas — son la base de todo.

---

## Hora 3 — Pipeline liviano (2:00 – 3:00)

**Objetivo:** liviano deployado, ejecutándose por cron, poblando raw tables.

### 2:00 – 2:30 — Tasks del liviano
- `src/shared/config.py` con pydantic-settings.
- `src/shared/db.py` con engine SQLAlchemy y session factory.
- `src/shared/http_clients.py` con clientes httpx tipados.
- `src/shared/schemas.py` con modelos pydantic para respuestas WooCommerce y FX.
- `src/pipelines/light/tasks.py`:
  - `fetch_fx_rates` con `cache_policy=INPUTS`, `cache_expiration=14min`.
  - `fetch_store_prices` con misma caché, parametrizada por `store_id`.
  - `validate_response_health`.
  - `quick_drift_check` (consulta último snapshot, compara, emite event si >5%).
  - `upsert_raw_tables` con SQL UPSERT.

### 2:30 – 2:50 — Flow + deployment
- `src/pipelines/light/flow.py` con `@flow`, llamando tasks en orden con asyncio para los 3 stores en paralelo.
- `src/deploy/deploy_light.py` que registra el deployment con cron `*/15 * * * *` en `light-pool`.
- Crear el work pool: `prefect work-pool create light-pool --type process`.
- Levantar worker: `prefect worker start --pool light-pool` (en otro contenedor del compose).

### 2:50 – 3:00 — Validación
- Trigger manual del deployment desde UI.
- **Verificar:** run termina en estado Completed, datos en `app.price_snapshots` y `app.fx_rates`.
- Trigger una segunda vez en el mismo minuto. **Verificar:** tasks aparecen como Cached.
- Commit: `feat(light): add light pipeline with caching and idempotent upserts`.

**Deliverable hora 3:** liviano corre por cron, datos persistiendo, idempotencia + caché demostrables.
**Si te atrasas:** corta `validate_response_health` y `api_health_log`. Son enriquecimiento, no core.

---

## Hora 4 — Pipeline pesado (3:00 – 4:00)

**Objetivo:** pesado deployado, ejecutándose en contenedor aislado.

### 3:00 – 3:20 — Imagen heavy
- `docker/heavy.Dockerfile` con `python:3.12-slim`, uv, pandas, prefect-client.
- Construir: `docker build -t heavy:latest -f docker/heavy.Dockerfile .`
- Verificar imagen disponible en daemon local.

### 3:20 – 3:50 — Tasks + flow del pesado
- `src/pipelines/heavy/tasks.py`:
  - `load_day_window` con SQLAlchemy → DataFrame.
  - `normalize_to_usd` con merge_asof o join temporal con FX.
  - `detect_drift` calcula `drift_pct = (price_usd - canonical) / canonical`.
  - `classify_severity` con bins configurables.
  - `infer_cause_hint` cruza con change_events y movimiento FX del día.
  - `aggregate_report` produce stats por país.
  - `write_outputs` con UPSERT.
  - `cleanup_old_raw` con flag `enable_cleanup` configurable.
- `src/pipelines/heavy/flow.py` con `@flow`.

### 3:50 – 4:00 — Deployment + Docker work pool
- `src/deploy/deploy_heavy.py` registra deployment en `heavy-pool` con cron `0 3 * * *` y `image="heavy:latest"`.
- Crear pool: `prefect work-pool create heavy-pool --type docker`.
- Levantar Docker worker en compose con socket Docker montado.
- Trigger manual.
- **Verificar:** `docker ps` durante la run muestra contenedor `heavy:latest` efímero. Tablas `daily_price_reports` y `price_alerts` pobladas al terminar.
- Commit: `feat(heavy): add heavy pipeline with docker work pool isolation`.

**Deliverable hora 4:** pesado corre en contenedor aislado, outputs en DB, idempotencia verificada manualmente (re-ejecutar mismo día reemplaza).
**Si te atrasas:** corta `infer_cause_hint`. Mejora del análisis pero no core. Recorta tests si llega a ser necesario.

---

## Hora 5 — Deploy en VM + Tests (4:00 – 5:00)

**Objetivo:** sistema corriendo en VM con HTTPS y basic auth. Tests críticos pasando.

### 4:00 – 4:20 — Caddy y DNS
- Configurar registro A en DuckDNS apuntando a IP de la VM.
- `caddy/Caddyfile` con dominio, basic auth, reverse proxy a `prefect-server:4200`.
- Generar password y hash: `caddy hash-password`.
- Agregar servicio `caddy` al `docker-compose.prod.yml`.

### 4:20 – 4:50 — Deploy en VM
- `scripts/deploy_vm.sh` con: clone repo, setup `.env.production`, `docker compose -f compose.yml -f compose.prod.yml up -d --build`, ejecutar migraciones, registrar deployments, levantar workers.
- SSH a VM, ejecutar el script.
- **Verificar:** `https://<subdominio>.duckdns.org` pide basic auth y luego carga Prefect UI con candadito verde.
- Triggear liviano y pesado desde la UI pública.
- Commit: `feat(infra): add caddy reverse proxy and vm deploy script`.

### 4:50 – 5:00 — Tests críticos
- `tests/unit/test_drift_detection.py` — 3-4 casos.
- `tests/integration/test_idempotency_light.py` — ejecuta tasks 2x, verifica no duplica.
- `tests/integration/test_idempotency_heavy.py` — ejecuta flow 2x mismo día, verifica reemplaza.
- Commit: `test: add unit and integration tests for core logic`.

**Deliverable hora 5:** URL pública accesible con HTTPS y auth, sistema corriendo, 3 tests críticos pasando.
**Si te atrasas:** prioriza el deploy. Tests pueden quedar en 1 unit + 1 integration si el reloj aprieta.

---

## Hora 6 — Documentación y entrega (5:00 – 6:00)

**Objetivo:** README completo, screencast grabado, PR creada.

### 5:00 – 5:25 — README completo
- Estructura definida en el documento de definiciones (sección 12).
- Diagrama de arquitectura: ASCII art en bloque de código o imagen simple.
- Sección "Decisiones técnicas" honesta y concreta.
- Sección "Idempotencia" con los 3 mecanismos.
- Sección "Cómo levantarlo" copiable.
- Sección "Segunda iteración" con 5-7 items legítimos.
- Commit: `docs: add complete readme`.

### 5:25 – 5:50 — Screencast
- Dejar la VM con datos acumulados de las últimas horas.
- Cerrar notificaciones, pestañas, Slack.
- Preparar 2 terminales: una con `psql`, otra con `docker ps`.
- Tener queries pre-armadas en `reports/queries.sql`.
- **Dry run** sin grabar (≤2 min).
- Grabar (1 toma limpia, ~3 min).
- Subir a Loom o YouTube unlisted.

### 5:50 – 6:00 — PR + cierre
- Push de la rama de trabajo.
- Crear PR a `main` con descripción según plantilla del documento de definiciones.
- Incluir en la PR: link al screencast, URL pública, credenciales basic auth.
- Auto-merge la PR si la rama es la única rama, o mergear directamente.
- Verificación final: la URL pública carga, los pipelines siguen corriendo.

**Deliverable hora 6:** entregables completos según pre-trabajo del documento de definiciones.

---

## Bloques opcionales (si vas adelantado)

Solo se activan si llegas con tiempo de sobra a algún punto del plan. Orden de prioridad si tienes 30, 20 o 10 minutos extras:

### Si tienes 30 min extras
- **CI mínimo en GitHub Actions** (lint + format + unit tests).
- Mensajes de log estructurados con JSON formatter en producción.

### Si tienes 20 min extras
- Tests adicionales: `test_severity_classifier.py`, `test_change_detection.py`.
- Notification hook en Prefect en caso de failure (mock, escribir a tabla de alerts).

### Si tienes 10 min extras
- Diagrama de arquitectura como imagen (excalidraw export → png en `docs/`).
- Refinar el README con pequeñas correcciones.

---

## Política de recortes (si vas atrasado)

Orden estricto de recortes, en este orden:

1. **CI** (Hora 6 / opcional). Primero en irse.
2. **`infer_cause_hint`** (Hora 4). Reduce el pesado a drift puro sin distinguir causas.
3. **`api_health_log`** y `validate_response_health` (Hora 3). Reduce el liviano a captura pura.
4. **Tests reducidos a 1 unit + 1 integration** (Hora 5).
5. **Screencast a 90 segundos** mínimo (Hora 6).
6. **Si no logras deploy en VM:** entrega solo local con explicación honesta en README + screencast del local funcionando. **Mucho peor entregar nada que entregar local con explicación.**

---

## Checkpoints clave

Validar al final de cada hora. Si algún checkpoint falla, decidir entre recuperar o recortar.

| Hora | Checkpoint | Si falla |
|---|---|---|
| 1 | UI Prefect local + VM accesible | Simplificar compose si UI no levanta. Continuar local sin VM hasta hora 5 |
| 2 | Schema en DB + mock respondiendo | Saltar Alembic, crear schema con SQL directo en init de Postgres |
| 3 | Liviano corre por cron + datos en raw | Reducir a 1 store si los 3 dan problema |
| 4 | Pesado corre en container aislado | Si Docker worker falla, fallback a Process worker con imagen distinta — pierdes señal de aislamiento pero salvas la entrega |
| 5 | URL pública con HTTPS funcional | Si Caddy/DuckDNS falla, exponer por IP cruda con HTTP. Mencionar en README como deuda. |
| 6 | Screencast subido + PR creada | No hay fallback. Esto se entrega sí o sí. |
