# Progreso

Última actualización: 8 de noviembre de 2026. Ramas: `hito-1` (tareas 1 a 5) y `hito-2` (tareas 6 a 11, sale de `hito-1`).

## Hito 1 — Base (rama `hito-1`)

| Tarea | Estado | Test de aceptación |
|---|---|---|
| 1. Monorepo, Compose, CI | Hecha | `test_smoke.py`: la app responde `/health` |
| 2. Esquema, migraciones y RLS | Hecha | `test_rls.py`: un tenant no lee ni escribe filas de otro; las FK no cruzan tenants; auditoría solo inserción; alcances anidados no pierden la RLS |
| 3. LLMClient, Embedder, uso y costo | Hecha | `test_llm_usage.py`: cada llamada guarda tokens (incluida la caché) y costo |
| 4. Cola de jobs y worker | Hecha | `test_jobs.py`: un job fallido se reintenta sin duplicar efectos; dos workers no toman el mismo; un worker caído se recupera |
| 5. Empresa Acme y API simulada | Hecha | `test_acme_api.py`: cada operación y código del OpenAPI se valida contra su esquema |

## Hito 2 — Agente de punta a punta (rama `hito-2`)

| Tarea | Estado | Test de aceptación |
|---|---|---|
| 6. RAG híbrido | Hecha | `test_rag.py`: Recall@5 = 23/23 (meta ≥ 90%), con embeddings **simulados** |
| 7. Ciclo del agente con 2 herramientas | Hecha | `test_agent.py`: resuelve los 10 casos guionados de la spec |
| 8. Guardián y aprobaciones | Hecha | `test_guardian.py`: ninguna acción de riesgo alto llega a la API sin aprobación; aprobar ejecuta una sola vez |
| 9. Control de fundamento y derivación | Hecha | `test_grounding.py`: pregunta sin fuente → deriva, no inventa; fuentes falsas y números sin respaldo se rechazan |
| 10. Auditoría encadenada | Hecha | `test_audit.py`: un caso se reconstruye completo; modificar o borrar un evento rompe la cadena |
| 11. Runner de evals en CI | Hecha | `test_evals.py`: un cambio que baja el puntaje bloquea el merge |

**Estado actual:** `make lint` limpio, `make test` con 64 tests en verde contra Neon, y `make evals` (simulado) con 10/10 casos resueltos y juez 1,00.

Decisiones tomadas sin tu aprobación (revisalas): `docs/decisions/001` a `011`.

## Qué significa "simulado"

Todavía no hay claves de API, así que Claude y Voyage se reemplazan por **transportes HTTP simulados** (`evals/simulated/`) que responden con el formato real de cada API. Corre el código de producción completo: SDK de Anthropic, cliente de Voyage, clasificador, RAG, ciclo, guardián, API de Acme, auditoría y costos.

**Lo que esto prueba:** el cableado y las garantías que viven en código (aislamiento por tenant, guardián, aprobaciones, verificación de identidad, fundamento, auditoría, cola, costos).

**Lo que NO prueba:** la calidad de Sonnet, Haiku, Opus y Voyage. El Claude simulado decide con reglas guionadas y los embeddings simulados son léxicos. Que el agente resuelva los 10 casos con el modelo real recién se sabe con claves.

## Qué falta para probarlo con claves reales

1. **Cargar las claves en `.env`:** `ANTHROPIC_API_KEY` y `VOYAGE_API_KEY`. No hace falta tocar código: `AnthropicProvider` y `VoyageEmbedder` las leen del entorno.
2. **Correr los evals reales:** `make evals`. Con `ANTHROPIC_API_KEY` definida pasa solo a modo real: Claude y Voyage reales, y la API de Acme simulada como sandbox. Las respuestas se guardan en `evals/.cache/`, así repetir sin cambios no se paga. Mirar `evals/last_report.json`. Cuando el resultado sea aceptable, fijar la línea base real con `python evals/runner.py --update-baseline` y hacer commit de `evals/baseline.real.json`.
3. **Medir el RAG con Voyage real:** `python evals/rag_recall.py`. Usa Voyage si hay `VOYAGE_API_KEY` y sale con error si Recall@5 queda por debajo de 90%.
4. **Lo primero que puede fallar con el modelo real** (probado solo con el simulado):
   - Devolver los bloques de la respuesta (incluido `thinking`) en el turno siguiente. Se envían tal como vienen de `model_dump()`; si la API rechaza algún campo, el ajuste va en `AnthropicProvider`.
   - Que Sonnet use siempre `responder`/`derivar` y complete `fuentes` y `factual`. Si contesta con texto suelto, el control de fundamento lo devuelve una vez y después deriva.
   - El `effort: low` del agente y del clasificador. Subirlo si los evals muestran errores de criterio.
   - El juez (Opus) usa una herramienta con `tool_choice` automático (los modelos 5.5 no permiten forzarla). Si no la usa, el caso puntúa 0.
5. **Verificar precios** en `backend/app/llm/pricing.py`: caché de Haiku 5.5 (estimada) y el precio de Voyage (decisión 003).
6. **CI con claves:** agregar `ANTHROPIC_API_KEY` y `VOYAGE_API_KEY` como secretos de GitHub y un paso de evals reales con la caché como artefacto. Hoy el CI corre el modo simulado.
7. **Generar los datos sintéticos con Opus** (una sola vez, se guardan en `evals/fixtures/acme/`): 500 conversaciones (las usa el hito 4) y completar a 20 documentos de políticas (hoy hay 11 escritos a mano).

## Pendientes conocidos (fuera de las tareas hechas)

- Ver que el CI de GitHub Actions quede verde: el repositorio es privado y no lo pude consultar desde acá.
- Rutas HTTP del agente y del panel (`/approvals/{id}/decision`, `/cases`, …): necesitan la autenticación del panel (hito 5). La lógica ya existe (`decide()`, `reconstruct_case()`).
- Avisar al cliente por WhatsApp cuando se decide una aprobación (hito 3).
- Anclar fuera de la base el último hash de auditoría de cada caso (decisión 010).
- `fallbacks` del lado del servidor ante rechazos del modelo: hoy se deriva (decisión 007).
- Proceso `worker` desplegable con sus handlers (llega con los hitos 3 y 4).

## Cómo probarlo localmente

```
python -m venv .venv
.venv\Scripts\python -m pip install -r backend/requirements-dev.txt
make lint
make test      # usa DATABASE_URL de .env (Neon); tarda unos 9 minutos por la latencia
make evals     # simulado sin ANTHROPIC_API_KEY; real con la clave
python evals/rag_recall.py
```

Para ver el agente en acción sin claves, `evals/simulated/stack.py` arma todo el sistema con Acme: `build_simulated_stack(conn).send("lucia", "¿Dónde está mi pedido 1001?")`.
