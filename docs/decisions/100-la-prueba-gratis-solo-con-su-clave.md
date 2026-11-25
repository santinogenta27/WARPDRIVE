# 100 — La prueba gratis usa solo su clave, y su clave la usa solo la prueba

**Contexto.** Con la decisión 099, el armado de la prueba gratis usaba `TRIAL_GEMINI_API_KEY` y, si
faltaba, `GEMINI_API_KEY`. El chat de la prueba seguía siendo el agente de la plataforma: Groq,
con Gemini de respaldo, y los embeddings de consulta de la plataforma. Una prueba podía gastar la
cuota de los clientes, y al revés.

**Opciones.**
1. Guardar la clave de la prueba como "clave propia" de cada empresa de prueba (decisión 049).
   Quedaría copiada, cifrada, en cada empresa, y seguiría ahí al convertirla en piloto.
2. Un agente de prueba en `app/trial/`, elegido por un parámetro opcional de `build_agent`.

**Recomendación (aplicada): 2.**

- **Un solo lugar lee la variable:** `app/trial/keys.py`. Un test recorre `backend/app`, `evals` y
  `mocks` y verifica que no aparezca en ningún otro archivo.
- **Todo con esa clave:** el armado, los embeddings de fondo y el chat. El chat usa el mismo `Agent`,
  con un proveedor y un embedder de Gemini con esa clave (`TrialAgent`), por `LLMClient` y con el
  uso en `usage`.
- **Sin respaldo de la plataforma:** ni Groq, ni `GEMINI_API_KEY`, ni Voyage. Como el armado y la
  búsqueda del chat usan el mismo embedder, los vectores siempre son compatibles.
- **Sin clave o sin cuota:**
  - el proveedor lanza `TrialBusy`, una `QuotaExhausted`;
  - el agente deriva el caso como cualquier empresa (decisión 098), así que el mensaje del visitante
    queda guardado;
  - el visitante ve "Our demo is very busy right now…" (o la versión en castellano), en el chat y en
    el historial;
  - durante 5 minutos no se vuelve a llamar a Gemini;
  - el armado sigue con búsqueda por texto (decisión 099).
- **`build_agent(conn, tenant_id, trial_agent=None)`:**
  - por defecto llama a `app.trial.models.agent_for`, que devuelve None si la empresa no es una
    prueba en curso;
  - para las demás empresas, Acme y Northwind, el agente es exactamente el de antes; solo se suma
    una consulta a `trials` por mensaje, como la que ya hace el límite de mensajes de la prueba.
- **Una prueba convertida en piloto** ya no figura como prueba en curso, así que vuelve al agente
  de la plataforma.
