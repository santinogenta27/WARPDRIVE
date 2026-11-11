# 018 — Chat web (tarea 12b) y verificación por código al email

**Estado:** adoptada (9 de noviembre de 2026). Revisar.

## Contexto

La tarea 12b pide un chat web detrás de la interfaz `Channel`, con el mismo agente, guardián y auditoría. La spec v1.1 agrega:
- Respuestas por Server-Sent Events.
- Sin login: verificación por código al email para los casos con datos de cuenta.
- La demo usa siempre el tenant Acme, con un límite de 20 mensajes por sesión.

Para verificar hay que saber el email de cada cliente, pero la API de Acme no lo tenía. Tampoco hay todavía un proveedor de email.

## Decisiones

- **Canal web** (`app/channels/web/`): `WebChannel` implementa `Channel`. En `receive()`, la sesión hace de firma. `send()` deja la respuesta en una bandeja que la ruta envía por SSE. El agente corre dentro del pedido HTTP, en una transacción: sin cola ni worker, porque el navegador espera la respuesta abierta. Si falla, se revierte todo y el mensaje no se descuenta.
- **SSE sobre un POST:** la página lee el stream con `fetch`. `EventSource` solo hace GET y el mensaje va en el cuerpo. Eventos: `typing`, `message`, `done`, `limit`, `error`.
- **Sesiones** (`web_sessions`, migración 0010): anónimas, con RLS por tenant. Se guarda solo el hash del token. Cada sesión es un contacto, identificado como `web:<hash>`. El límite de 20 mensajes se controla con la fila bloqueada, así dos pestañas no lo pasan.
- **Tenant de la demo:** fila de `channels` de tipo `web` con la dirección `web:acme-demo`, que asigna `make demo-setup`.
- **Verificación por código, en el agente y no en el canal**, así sirve también para WhatsApp cuando el teléfono no coincide (spec):
  - El agente pide el email y guarda la consulta pendiente (`identity_verifications`, migración 0009).
  - Busca el email en la API del cliente. La consulta se configura por tenant en `settings.identity_lookup` y para Acme es `GET /customers?email=`, agregada a la API simulada. La hace el guardián, que es el único que llama a la API del cliente.
  - Envía el código: 6 dígitos, vence en 10 minutos, 3 intentos. Se guarda solo su hash.
  - Si el email no existe, la respuesta es la misma, para no revelarlo.
  - Al confirmar, el contacto queda verificado 30 días como ese cliente (`contacts.verified_phone_hash`) y el agente responde la consulta pendiente.
  - Los controles de dueño del guardián comparan con ese teléfono en lugar del teléfono del contacto, que en web no existe.
- **`Mailer`** (`app/mailer.py`): interfaz de la spec. Por ahora `ConsoleMailer` escribe el email en la consola del servidor y no envía nada. En los tests y evals, `RecordingMailer` los guarda.
- **Página** (`app/channels/web/static/index.html`):
  - Un solo archivo, sin recursos externos, que se sirve en `/` y `/chat`.
  - Pensada primero para el celular y con modo oscuro.
  - Muestra la negrita estilo WhatsApp (`*texto*`).
  - Tiene un aviso discreto de que es una demo de Enterprise Data AI, con la ayuda para probarla.

## Pendiente

- Proveedor de email real detrás de `Mailer` (spec: "Proveedor transaccional").
- Widget instalable con una línea de JavaScript (spec). Hoy es una página.
- Límite por IP o captcha si la demo se publica: el límite de 20 es por sesión y una sesión nueva se crea sin costo.
- Verificación por coincidencia de teléfono en WhatsApp (spec, 30 días). Hoy se usa el campo `verified` del fixture.
