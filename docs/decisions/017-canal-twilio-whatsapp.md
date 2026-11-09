# 017 — Canal `twilio_whatsapp` e interfaz `Channel`

**Estado:** adoptada (9 de noviembre de 2026). Revisar.

## Contexto

Meta bloqueó la cuenta de WhatsApp Business de la demo (error 131031; `health_status` pide un método de pago válido y la verificación de la empresa). Para probar el agente por WhatsApp mientras tanto se usa la prueba de WhatsApp de Twilio ("Try out WhatsApp"): el cliente se une con `join <palabra>` y Twilio reenvía sus mensajes a un webhook. La spec v1.1 pide una interfaz `Channel` con `receive()` y `send()`, donde el canal es solo transporte.

## Decisiones

- **Interfaz `Channel`** (`app/channels/base.py`): `kind`, `receive(url, body, headers)`, que valida la firma y devuelve mensajes, y `send(to, body)`, que devuelve el id del mensaje. Ahí viven también `InboundMessage`, el alta del contacto y el borrado del teléfono del job, que antes estaban en el handler de Meta.
- **Handler común** (`app/channels/inbound.py`): rutea por número y tipo de canal, corre el mismo agente, guardián y auditoría que Meta, y responde con `channel.send()`. Los envíos quedan auditados como `whatsapp_sent` con `channel: twilio_whatsapp`.
- **La Cloud API de Meta queda como estaba:** sigue con su handler por las plantillas y la ventana de 24 h. Pasarla a la interfaz queda para la tarea 12b, junto con el canal web.
- **Firma:** se valida `X-Twilio-Signature`, que es HMAC-SHA1 con el auth token sobre la URL pública más los parámetros ordenados. Detrás del túnel, uvicorn reconstruye la URL `https://` con los encabezados `X-Forwarded-*` que manda cloudflared.
- **Sin SDK de Twilio:** son dos llamadas HTTP (recibir un formulario y POST a `Messages.json`), así que se hacen con `httpx`, que ya es dependencia.
- **Credenciales en el entorno** (`TWILIO_*`), no cifradas en `channels`: es una cuenta de prueba única para la demo. La fila de `channels` (tipo `twilio_whatsapp`, migración 0008) solo rutea el número de envío al tenant Acme. Si se usa Twilio con varias empresas, el auth token pasa a `access_token_enc`, como el token de Meta.
- **Mismo contacto:** el remitente se guarda como `WaId` (por ejemplo `5491155550000`), igual que el `wa_id` de Meta, así el mismo cliente es el mismo contacto en los dos canales.
- **Errores de envío:** un 4xx de Twilio (salvo 429) no se reintenta, para no volver a correr el agente ni gastar cuota. Se registra `whatsapp_send_failed`. Un 5xx o un 429 se reintenta.
- **Respuesta al webhook:** TwiML vacío. La respuesta la manda el worker por la API, no en el cuerpo del webhook.

## Pendiente

- Ventana de 24 h y plantillas por Twilio (los Content Templates): hoy solo se responde dentro de la conversación.
- Twilio no manda la hora del mensaje en el webhook: se usa la hora de llegada.
