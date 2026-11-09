# 012 — Gemini gratis como alternativa a Claude y Voyage

**Estado:** adoptada a pedido (rama `gemini-gratis`) para probar el agente sin costo. Revisar antes de fusionar.

## Contexto

Todavía no hay `ANTHROPIC_API_KEY` ni `VOYAGE_API_KEY`, así que el agente solo se probó con modelos simulados. El pedido fue probarlo gratis con la API de Gemini (`GEMINI_API_KEY`), sin tocar el resto del agente y usando solo modelos del nivel gratuito. La spec fija Claude y Voyage: esto es una alternativa para desarrollo, no un cambio de stack.

## Decisiones

- **Elección por entorno** (`app/llm/factory.py`): modelos con Claude si hay `ANTHROPIC_API_KEY`, si no con Gemini si hay `GEMINI_API_KEY`. Embeddings con Voyage si hay `VOYAGE_API_KEY`, si no con Gemini. Cargar la clave de Anthropic devuelve todo a Claude sin tocar código.
- **`GeminiProvider` detrás de `LLMClient`** (`app/llm/gemini.py`), por la API REST con `httpx`, que ya era dependencia. No se agrega el SDK de Google. El agente sigue hablando en el formato de mensajes de Claude y el proveedor lo traduce:
  - `tools` pasa a `functionDeclarations` con `parametersJsonSchema`, que acepta el JSON Schema tal cual (incluido `additionalProperties`).
  - `tool_use` y `tool_result` pasan a `functionCall` y `functionResponse`, con el mismo id.
  - La `thoughtSignature` que devuelve Gemini 3.x se guarda en el bloque (`thought_signature`) y se reenvía en el turno siguiente, como pide la API.
  - `output_config.effort` pasa a `thinkingConfig.thinkingLevel`.
  - Un bloqueo o un corte por seguridad se traduce como `refusal`, y el agente ya deriva en ese caso.
- **Modelos del nivel gratuito** (página de precios de Google, consultada el 9 de noviembre de 2026):
  - Agente (`claude-sonnet-5-5`): `gemini-3.6-flash`, estable.
  - Clasificador (`claude-haiku-5-5`): `gemini-3.5-flash-lite`, estable.
  - Juez de evals: `gemini-3.5-flash-lite`, fijado en el runner para el modo `gemini` (`JUDGE_MODELS`). Flash permite 20 pedidos por día y una corrida completa con el juez en Flash usaba unos 25; con el juez en Flash-Lite, Flash queda en unos 11. Si otro código pide `claude-opus-5-5`, el mapeo del proveedor lo sigue llevando a Flash.
  - Embeddings: `gemini-embedding-2` con `outputDimensionality` 1024, igual que la columna `vector(1024)`. La API los normaliza sola. Este modelo no acepta `task_type`, así que la tarea va como prefijo del texto: `title: none | text: …` para documentos y `task: search result | query: …` para consultas.
  - Se descartaron `gemini-3.8-flash`, que es nuevo, y `gemini-2.5-flash`, que está deprecado y desde el 1 de diciembre de 2026 se redirige a 3.5 Flash-Lite.
- **Costo cero:** los tres modelos están en `pricing.py` con precio 0 (nivel gratuito). `LLMClient` ahora registra en `usage` el modelo que respondió y no el que pidió el agente. Con Claude es el mismo; con Gemini, `usage` dice `gemini-3.6-flash` a costo 0 en vez de inventar un costo de Sonnet.
- **Límites del nivel gratuito** (`GeminiTransport`):
  - Pausa mínima entre llamadas: `GEMINI_MIN_INTERVAL`, 6 s por defecto, unas 10 por minuto.
  - Ante 429, 408 o 5xx: hasta 6 reintentos. Espera lo que indica `RetryInfo` o, si no lo indica, una espera exponencial con jitter (2, 4, 8… hasta 60 s).
  - Si la cuota agotada es la diaria (`quotaId` con `PerDay`) no reintenta y lanza `GeminiQuotaExhausted(daily=True)`.
  - Google no publica los límites por modelo en la documentación, solo en AI Studio. Por eso la pausa es configurable.
- **Evals que se pueden retomar:** en los modos reales, el runner guarda cada caso calificado en `evals/.cache/progress-<modo>.json`. Si se agota la cuota, informa cuántos casos faltan y sale con código 2; la próxima corrida sigue desde el primero que falta. El modo simulado (CI) siempre corre completo. `--restart` descarta el avance.
- **Modo `gemini` separado:** tiene su propia línea base (`evals/baseline.gemini.json`) y su propia caché (`evals/.cache/gemini/`). La caché es por entrada exacta y la clave incluye el nombre de modelo del agente, así que sin esta separación una respuesta de Gemini podría servirse como si fuera de Claude.

## Consecuencias

- Cambiar de embedder obliga a reindexar, porque los vectores de Voyage y de Gemini no son comparables. Los evals reindexan en cada corrida.
- Lo que se use del nivel gratuito, Google puede usarlo para mejorar sus productos. Solo se mandan datos de la empresa de demo Acme, nunca datos reales de clientes.
- Los resultados con Gemini no predicen los de Claude. Sirven para probar el cableado contra un modelo real y encontrar dónde el prompt depende del modelo.
