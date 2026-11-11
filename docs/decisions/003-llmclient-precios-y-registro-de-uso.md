# 003 — LLMClient, precios y registro de uso

**Estado:** adoptada durante la corrida sin supervisión del hito 1 (opción más simple). Revisar.

## Contexto

La spec pide `LLMClient`, `Embedder` y registrar tokens y costo de cada llamada, pero no fija la tabla de precios, qué tokens contar ni qué modelo de Voyage usar.

## Decisiones

- **Un solo punto de entrada.** `LLMClient.complete()` valida que el modelo tenga precio *antes* de llamar (sin precio no hay costo, así que no se llama), llama al proveedor y registra en `usage`. `Embedder` (Voyage) registra igual, con `kind = 'embedding'`.
- **Proveedor intercambiable.** `LLMProvider` es un protocolo; `AnthropicProvider` usa el SDK oficial con timeout de 30 s y 3 reintentos, como pide la sección "Fallas externas" de la spec. La derivación tras agotar reintentos le toca al agente (hito 2).
- **Tokens registrados:** entrada, salida, escritura y lectura de caché de prompts. El costo se guarda con 10 decimales.
- **Registro en la transacción del llamador** (`PostgresUsageRecorder(conn)`), bajo `tenant_scope`: si el job falla y revierte, el uso queda igual de consistente que el resto del trabajo.

## Precios (USD por millón de tokens)

| Modelo | Entrada | Salida | Escritura caché | Lectura caché | Fuente |
|---|---|---|---|---|---|
| claude-opus-5-5 | 4 | 20 | 5 | 0,20 | Tabla de modelos de Anthropic (entrada, salida, lectura); escritura = 1,25 × entrada |
| claude-sonnet-5-5 | 2 | 10 | 2,5 | 0,20 | Ídem |
| claude-haiku-5-5 | 0,10 | 0,50 | 0,125 | 0,01 | Entrada y salida de la tabla (prompts hasta 100K tokens); caché **estimada** con la regla 1,25× / 0,1× |
| voyage-multilingual-2 | 0,12 | — | — | — | **Verificar** en la página de precios de Voyage |

Pendiente de verificar contra las páginas de precios oficiales: la caché de Haiku 5.5, el tramo de Haiku sobre 100K tokens (0,50 / 2,50) y el precio de Voyage. Se cambian en `backend/app/llm/pricing.py`.

## Nota técnica

El SDK de Anthropic 2.x usa `httpx2` (misma API que `httpx`). Los tests inyectan un transporte simulado de `httpx2` en el SDK real, así prueban el mapeo de la respuesta sin red ni claves. Voyage se llama con `httpx` (ya era dependencia).
