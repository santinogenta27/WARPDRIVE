# 014 — Webhook de WhatsApp, envío y worker

**Estado:** adoptada (tarea 12). Revisar.

## Contexto

La tarea 12 pide el webhook de la WhatsApp Cloud API, la verificación de firma y el envío, y la spec agrega que el webhook responde 200 en menos de 1 s y deja el trabajo en la cola. No define el formato del job, qué hacer con mensajes que no son texto antes de la tarea 12c, ni cómo guardar el teléfono que hace falta para responder.

## Decisiones

- **Webhook** (`app/channels/whatsapp/webhook.py`):
  - El `GET` responde el `hub.challenge` si el `hub.verify_token` coincide con `WHATSAPP_VERIFY_TOKEN`; si no, devuelve 403.
  - El `POST` verifica `X-Hub-Signature-256`, que es un HMAC-SHA256 del cuerpo crudo con `WHATSAPP_APP_SECRET`, comparado en tiempo constante. Si la firma falla devuelve 401 y no encola nada.
  - Cada mensaje queda como job `whatsapp_message` con clave de idempotencia `whatsapp:<wamid>`, así un reenvío de Meta no duplica nada. Los avisos de estado (entregado, leído) se ignoran.
- **Worker** (`python -m app.jobs`, `make worker`): corre el agente y responde dentro de la transacción del job. Si el envío falla se revierte todo y el job se reintenta, con la espera exponencial del worker de la tarea 4.
- **Envío:** Graph API `v27.0`, que es la más reciente del changelog de Meta en noviembre de 2026. Cada envío queda en la auditoría del caso como `whatsapp_sent`, con su wamid.
- **Celulares de Argentina:** WhatsApp informa el número como `549…`, pero la lista de números permitidos del número de prueba lo guarda sin el 9. Si Meta responde 131030 ("no está en la lista"), se reintenta una vez sin el 9.
- **Teléfono:** el contacto se guarda solo con el hash, como ya se hacía. El job necesita el número en claro para responder, y se borra del job en la misma transacción en que se envía la respuesta.
- **Tipos de mensaje:**
  - Stickers y reacciones se ignoran.
  - Los demás tipos que no son texto (audio, imagen, video) reciben "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribís?" hasta la tarea 12c.
- **Ruteo provisorio:** en esta tarea el número se asigna al tenant en `tenants.settings.whatsapp.phone_number_id`. La tarea 12e lo pasa a la tabla `channels`.
- **Demo:** `make whatsapp-setup` crea una sola vez un tenant Acme permanente (`settings.demo = 'acme'`) y le asigna el número de prueba. La API de Acme corre simulada dentro del worker, como en los evals, y sus datos viven en memoria.

## Pendiente

- Agrupar ráfagas de mensajes (4 s), audios e imágenes: tarea 12c.
- Límite de 20 mensajes por minuto por contacto.
- Si el modelo falla en todos los reintentos del job, el cliente no recibe respuesta. La spec pide derivar y avisar.
