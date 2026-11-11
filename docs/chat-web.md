# Probar el chat web de Acme Store

El chat web es la tarea 12b: el mismo agente, guardián y auditoría que por WhatsApp, en una página. Funciona en la PC y en el celular.

## Levantarlo

1. Una sola vez, o cuando cambia algo de la demo: `make demo-setup`. Asigna el chat web a Acme Store; es el mismo comando que `make whatsapp-setup`.
2. `make api`. La página queda en http://localhost:8000.
3. Para abrirlo desde el celular o compartirlo: `cloudflared tunnel --no-autoupdate --url http://localhost:8000`, y abrí la URL `https://….trycloudflare.com` que muestra cloudflared. El worker (`make worker`) no hace falta para el chat web: solo para WhatsApp.

## Qué probar

- **Consultas generales:** horarios, cuotas, envíos, cambios. Responde con los documentos de Acme.
- **Un pedido:** "¿Dónde está mi pedido 1001?". El agente pide el email de la compra.
  - Respondé `lucia@example.com`.
  - **El código no llega por email:** todavía no hay proveedor de email, así que aparece en la terminal de `make api`, en una línea `[Mailer de consola, no se envió]`.
  - Escribí el código en el chat. El agente confirma la verificación y responde la consulta del pedido.
  - Otros clientes de la demo: `martin@example.com` (pedidos 1003 y 1005) y `sofia@example.com` (pedido 1006).
- **Límite:** cada sesión tiene 20 mensajes. "Cómo probarla" → "Empezar de nuevo" abre una sesión nueva.

## Cómo funciona

- `POST /chat/session` crea una sesión anónima; la página la guarda en el navegador.
- `POST /chat/messages` devuelve la respuesta por Server-Sent Events: `typing`, `message`, `done`, y `limit` o `error` cuando corresponde.
- El código vence a los 10 minutos y admite 3 intentos. Si el email no está registrado, el agente responde lo mismo, para no revelarlo.
- El modelo es el de `GEMINI_AGENT_MODEL` en `.env` (o Claude, si hay `ANTHROPIC_API_KEY`). Con el nivel gratuito de Gemini, cada respuesta tarda unos segundos por la pausa entre llamadas.

Decisión 018.
