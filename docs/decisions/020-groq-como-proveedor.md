# 020 — Groq como proveedor de modelos

**Estado:** adoptada (9 de noviembre de 2026). Revisar.

## Contexto

El chat web es una demo pública. Con Gemini gratis la cuota diaria es baja y algunos modelos daban 503. Groq tiene un plan gratuito con modelos que llaman herramientas y una API compatible con la de OpenAI.

## Decisiones

- **`GroqProvider`** (`app/llm/groq.py`), detrás de `LLMClient`, como Gemini. Traduce los bloques de Claude a chat completions y de vuelta:
  - `tool_use` pasa a `tool_calls` con los argumentos en JSON.
  - `tool_result` pasa a mensajes `role: tool`.
  - `effort` pasa a `reasoning_effort`, solo en los modelos gpt-oss.
  - Las razones de corte se traducen a las de Claude.
- **Límites del plan gratuito**, medidos en la cuenta: 1000 pedidos por día y 8000 tokens por minuto, por modelo. El transporte lee `x-ratelimit-remaining-tokens` y espera a que se renueve el minuto antes de pasarse. También respeta `retry-after`. Si la espera pedida supera 90 s, es cuota diaria agotada (`GroqQuotaExhausted`).
- **Salidas mal generadas:** los modelos a veces devuelven un 400 `tool_use_failed` u `output_parse_failed`, por ejemplo cuando escriben el razonamiento en lugar de llamar a la herramienta. Se reintenta. Si falla 3 veces seguidas, se devuelve un rechazo (`stop_reason: refusal`) y el agente deriva sin inventar.
- **Elección por entorno** (`app/llm/factory.py`):
  - Claude si hay `ANTHROPIC_API_KEY`.
  - Si no, `LLM_PROVIDER` (`gemini` o `groq`). Sin esa variable, el que tenga clave.
  - Con las dos claves gratuitas, la otra queda de respaldo (`FallbackProvider`) cuando la elegida se queda sin cuota. `LLM_FALLBACK=off` lo apaga.
- **Embeddings:** siguen con Gemini, porque Groq no tiene. Cambiar de proveedor de modelo no obliga a reindexar.
- **Precio:** el plan gratuito no cobra, así que el costo se registra en 0. En `pricing.py` quedan anotadas las tarifas del plan Developer.
- **Evals:**
  - Modo `groq` con su línea base (`evals/baseline.groq.json`).
  - El juez usa `qwen/qwen3.8-27b`, para no gastar los tokens por minuto del agente.
  - Caché por modelo real: antes la clave era el nombre de Claude y dos modelos de Groq compartían respuestas.
  - Sin respaldo: se mide un solo modelo.

## Modelos comparados (10 casos guionados de Acme, mismo clasificador y juez)

| Agente | Resolución | Juez | Notas |
|---|---|---|---|
| **Groq `openai/gpt-oss-20b`** | **100%** | **0,89** | Elegido para el chat web |
| Gemini `gemini-3.5-flash-lite` | 90% | 0,80 | Deriva el caso 04 en lugar de explicar que ya se despachó |
| Groq `qwen/qwen3.8-27b` | 80% | 0,76 | Falla el cambio de dirección (03) y deriva el 04 |
| Groq `openai/gpt-oss-120b` | 70% | 0,63 | En 3 casos insiste en llamar a una herramienta inexistente y termina derivando |

`llama-3.3-70b-versatile` y `llama-3.1-8b-instant` no están disponibles para la cuenta (404).

## Configuración del chat web

`LLM_PROVIDER=groq` y `GROQ_AGENT_MODEL=GROQ_CLASSIFIER_MODEL=openai/gpt-oss-20b`. Gemini hace los embeddings y queda de respaldo.

Agente y clasificador comparten los 8000 tokens por minuto del modelo, unos 3 o 4 mensajes de chat por minuto. Con más tráfico, el transporte espera, y si se agota la cuota diaria responde Gemini.
