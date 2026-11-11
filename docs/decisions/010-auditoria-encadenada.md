# 010 — Auditoría encadenada

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Decisiones

- **Cadena por caso** (no por tenant): cada caso tiene secuencia propia `seq = 1, 2, 3…` y cada evento guarda el hash del anterior. Reconstruir y verificar un caso no depende del resto del tenant, y los casos no compiten por un lock global.
- **Qué cubre el hash** (SHA-256 sobre JSON canónico): tenant, caso, secuencia, tipo, payload, fecha y hash anterior. Cambiar el contenido, borrar un evento (deja un hueco en la secuencia) o reordenar se detecta en `verify_chain`, que dice en qué evento se rompe.
- **Concurrencia:** agregar un evento bloquea la fila del caso (`FOR UPDATE`), así dos eventos no toman la misma secuencia; además hay índice único `(tenant_id, case_id, seq)`.
- **Eventos del agente:** `message_received`, `model_call` (propósito, modelo, tokens, costo en USD), `classified`, `rag_search` (fuentes e ids de fragmentos), `tool_call` (herramienta, parámetros, decisión del guardián, resultado, aprobación), `grounding_rejected`, `escalated` (resumen) y `final_action` (acción, mensaje, fuentes). Alcanza para la demo: "cada paso, fuente y costo".
- **Defensa en dos capas:** la tabla es solo inserción (permisos de `app_user` + trigger); la cadena detecta además cambios de alguien con acceso de dueño que desactive el trigger (el test lo simula).

## Pendiente

- Anclar periódicamente el último hash de cada caso fuera de la base (por ejemplo en el almacenamiento S3 con versionado). Sin eso, quien tenga acceso de dueño podría reescribir la cadena entera de un caso de forma consistente.
- Eventos de la respuesta humana y de la decisión de aprobaciones (llegan con el panel, hito 5).
