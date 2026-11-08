# 001 — Entorno local y dependencias base

**Estado:** adoptada durante la corrida sin supervisión del hito 1 (opción más simple). Revisar.

## Contexto

La spec pide Docker Compose con Postgres + pgvector para `make dev`. En la PC de desarrollo no hay Docker y Santino decidió no usar Docker Desktop. La base de desarrollo es un Postgres gestionado en Neon (`DATABASE_URL` en `.env`, que no se sube).

## Decisiones

1. **Base de datos de desarrollo: Neon.** `docker-compose.yml` existe igual (pgvector/pgvector:pg16) para quien tenga Docker, y el CI usa la misma imagen como servicio. Neon corre PostgreSQL 18; CI y Compose corren 16, que es lo que pide la spec. Lo que se usa (RLS, pgvector, `SKIP LOCKED`) se comporta igual en ambas.
2. **`make` en Windows.** El Makefile usa `.venv` si existe (Windows o Linux) y si no, el `python` del sistema (CI). `make dev` solo levanta la base con Compose; sin Docker, se usa Neon directamente.
3. **Configuración.** `app/config.py` lee variables de entorno y carga `.env` con un lector propio de 15 líneas, para no sumar `python-dotenv`.

## Dependencias agregadas

| Paquete | Por qué |
|---|---|
| fastapi, uvicorn | Backend definido en la spec |
| psycopg[binary] 3 | Driver de Postgres; SQL directo sin ORM (ver decisión 002) |
| anthropic | Proveedor de `LLMClient` (tarea 3) |
| httpx | Cliente HTTP para `Embedder` de Voyage (tarea 3) y `TestClient` de FastAPI |
| jsonschema | Validar respuestas de la API simulada contra su OpenAPI (tarea 5) y, más adelante, esquemas de herramientas |
| pytest | Tests (spec) |
| ruff, mypy | `make lint`: formato y tipos (CLAUDE.md) |

Versiones fijadas en `backend/requirements*.txt`.
