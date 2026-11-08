# Progreso

## Hito 1 — Base (rama `hito-1`)

| Tarea | Estado | Test de aceptación |
|---|---|---|
| 1. Monorepo, Compose, CI | Hecha | `test_smoke.py`: la app responde `/health` |
| 2. Esquema, migraciones y RLS | Hecha | `test_rls.py`: un tenant no lee ni escribe filas de otro; FKs no cruzan tenants; auditoría solo inserción |
| 3. LLMClient, Embedder, uso y costo | Hecha | `test_llm_usage.py`: cada llamada guarda tokens (incl. caché) y costo |
| 4. Cola de jobs y worker | Hecha | `test_jobs.py`: un job fallido se reintenta sin duplicar efectos; dos workers no toman el mismo |
| 5. Empresa Acme y API simulada | Hecha | `test_acme_api.py`: cada operación y código del OpenAPI se valida contra su esquema |

Decisiones tomadas sin aprobación: `docs/decisions/001` a `005`.

### Cómo probarlo

```
python -m venv .venv
.venv\Scripts\python -m pip install -r backend/requirements-dev.txt
make test     # necesita DATABASE_URL en .env (Neon) o el Postgres de docker compose
make lint
make evals
```

### Pendiente del hito 1

- Ver que el CI de GitHub Actions quede verde (el repositorio es privado; no lo pude consultar desde acá).
- Verificar precios de Haiku 5.5 (caché) y Voyage (`docs/decisions/003`).
- Generar con Opus las 500 conversaciones de Acme (necesita `ANTHROPIC_API_KEY`).
