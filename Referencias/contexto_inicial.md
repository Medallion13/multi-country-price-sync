# Contexto Inicial del Project

Documento que indica qué cargar al `project` de Claude antes de arrancar la implementación, y referencias útiles para tener a mano.

---

## 1. Archivos a subir al project

Subir estos tres archivos como **Project knowledge** en Claude.ai:

| Archivo | Origen | Rol en el project |
|---|---|---|
| `definiciones_base.md` | Generado en este chat | Fuente única de verdad de las decisiones |
| `plan_6_horas.md` | Generado en este chat | Plan cronometrado del trabajo |
| `prueba_tecnica.pdf` | PDF original que recibiste | Referencia del enunciado, sin nombre de empresa expuesto |

El master prompt referencia estos archivos por nombre, así que **mantén estos nombres exactos** al subirlos.

---

## 2. Referencias web útiles

Tener abiertas en pestañas durante el trabajo. No se suben al project, pero el master prompt instruye a Claude a pedirlas explícitamente cuando las necesite.

### Documentación oficial Prefect 3.6
- Quickstart: `https://docs.prefect.io/v3/get-started/quickstart`
- Schedule a flow: `https://docs.prefect.io/v3/tutorials/schedule`
- Work pools: `https://docs.prefect.io/v3/concepts/work-pools`
- Workers: `https://docs.prefect.io/v3/concepts/workers`
- Deployments: `https://docs.prefect.io/v3/concepts/deployments`
- Caching: `https://docs.prefect.io/v3/develop/task-caching`
- Docker work pool: `https://docs.prefect.io/v3/integrations/prefect-docker`

### Documentación oficial otros
- uv: `https://docs.astral.sh/uv/`
- Alembic: `https://alembic.sqlalchemy.org/en/latest/tutorial.html`
- SQLAlchemy 2.0: `https://docs.sqlalchemy.org/en/20/`
- FastAPI: `https://fastapi.tiangolo.com/`
- pydantic-settings: `https://docs.pydantic.dev/latest/concepts/pydantic_settings/`
- Caddy + Docker: `https://hub.docker.com/_/caddy`
- Caddyfile basicauth: `https://caddyserver.com/docs/caddyfile/directives/basic_auth`

### Servicios externos
- DuckDNS: `https://www.duckdns.org/`
- exchangerate.host (FX rates gratis sin key): `https://exchangerate.host/`
- Hetzner Cloud Console: `https://console.hetzner.cloud/`

### Herramientas
- Generador de hash bcrypt para Caddy: comando `caddy hash-password` (o web alternativa).
- Loom (para screencast): `https://www.loom.com/`

---

## 3. Pre-trabajo a validar antes de arrancar

Lista replicada del plan, con checkboxes para verificación rápida:

- [ ] Cuenta DuckDNS lista, subdominio reservado, token guardado.
- [ ] Cuenta Hetzner Cloud lista, método de pago verificado e identidad confirmada.
- [ ] Cuenta GitHub lista para crear repo nuevo.
- [ ] Editor con extensiones de Python y Docker.
- [ ] Cliente de DB instalado (psql, pgcli o DBeaver).
- [ ] Software de grabación de pantalla probado (OBS, Loom o nativo del SO).
- [ ] PDF original de la prueba leído íntegramente al menos una vez.
- [ ] Conexión a internet estable y entorno sin interrupciones planeadas durante 6 horas.

---

## 4. Información a comunicar a Claude en el primer mensaje

Cuando abras el primer chat dentro del project, después de que Claude haga su check-in inicial según el master prompt, prepárate para responder:

1. **Subdominio DuckDNS** elegido (para que Claude lo refleje en el Caddyfile y el README).
2. **Hora de arranque del cronómetro** (Claude marca el inicio del bloque 1).

---

## 5. Snippets útiles para el chat con Claude

Cuando necesites pasarle información rápida a Claude durante la sesión, estos formatos ahorran tiempo:

### Confirmar bloque
```
Arrancamos bloque [X]. Listo para diseño.
```

### Reportar checkpoint OK
```
Checkpoint hora [X] OK: [breve confirmación]. Siguiente bloque.
```

### Reportar problema
```
Bloqueado en [tarea]. Síntoma: [qué pasa]. Esperaba: [qué debería pasar].
Logs/error: [pegar].
```

### Pedir recorte
```
Voy [N] minutos atrasado. ¿Qué recorto según la política del plan?
```

### Pedir validación de versión
```
Antes de fijar [paquete], confirma versión actual contra PyPI.
```
