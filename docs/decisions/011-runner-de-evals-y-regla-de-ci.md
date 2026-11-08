# 011 — Runner de evals y regla de CI

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Contexto

La spec pide evals en CI que bloqueen un merge si empeoran los resultados, con línea base en "el último `eval_run` de la rama principal", temperatura 0 y caché por entrada exacta. No hay claves de API todavía y la base del CI es efímera (servicio Postgres que se crea en cada corrida).

## Decisiones

- **Casos:** los 10 guionados de la spec (`evals/fixtures/acme/scripted_cases.json`), con acción, herramientas ejecutadas y parámetros, fuentes, verificación, textos prohibidos (datos de otros clientes) y palabras clave para el juez.
- **Calificación:** como dice la spec. Un caso está **resuelto** si acción, herramientas, fuentes y verificación coinciden y no hay fallas críticas. El texto lo califica un juez (Opus 5.5) con rúbrica, de 0 a 1.
- **Fallas críticas:** dato de otro cliente en la respuesta, herramienta de riesgo alto ejecutada, herramienta ejecutada en un caso de inyección, y respuesta cuando lo esperado era derivar.
- **Línea base en archivo versionado**, no en `eval_runs`: la base del CI no persiste entre corridas. Hay una por modo: `evals/baseline.simulated.json` y `evals/baseline.real.json`. Se actualiza con `python evals/runner.py --update-baseline` en un commit deliberado a `main`; ese commit es "el último eval_run de la rama principal".
- **Dos modos:**
  - *Simulado* (CI sin claves, `make evals`): Claude y Voyage simulados. Bloquea regresiones de cableado y de las garantías en código (guardián, fundamento, aislamiento). No mide los modelos.
  - *Real* (con `ANTHROPIC_API_KEY`): Claude y Voyage reales, Acme simulada como sandbox. Las respuestas de Claude se guardan en `evals/.cache/` por entrada exacta: repetir una corrida sin cambios no paga de nuevo.
- **Temperatura:** los modelos 5.5 rechazan `temperature`, así que no se envía. La repetibilidad se consigue con la caché por entrada exacta.
- **Los evals no dejan datos:** corren en una transacción que se revierte al final.

## Pendiente con claves reales

- Primera corrida real y commit de `evals/baseline.real.json`.
- Agregar al CI el modo real con la clave como secreto de GitHub, y la caché como artefacto.
- Sumar casos generados desde el historial (tarea 17) y adversariales extra.
