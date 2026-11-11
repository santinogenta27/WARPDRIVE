# 019 — Chat web en Render y límites de uso de la demo pública

**Estado:** adoptada (9 de noviembre de 2026). Revisar.

## Contexto

El chat web necesita un link fijo: el túnel de cloudflared cambia de URL en cada arranque y depende de la PC. Se despliega en Render, plan gratis, y la base sigue en Neon. Como la demo es pública y el modelo es el nivel gratuito de Gemini, que tiene cuota diaria, hace falta limitar el uso.

## Decisiones

- **Render con Blueprint** (`render.yaml`):
  - Un servicio web Python en el plan gratis, en la región `virginia`. Neon está en São Paulo y Render no tiene región en Sudamérica.
  - Despliega la rama `hito-3`.
  - Al arrancar aplica las migraciones pendientes (`python -m app.db.migrate`, idempotente y con lock) y levanta uvicorn.
  - Los secretos (`DATABASE_URL`, `GEMINI_API_KEY` y `ENCRYPTION_KEY`) son `sync: false`: se cargan en Render, nunca en el repositorio.
  - Solo corre la API. El worker de WhatsApp no hace falta para el chat web.
- **Tres límites**, controlados con la sesión y la IP bloqueadas antes de correr el agente, así una consulta rechazada no gasta cuota:
  - 20 mensajes por sesión (spec).
  - `WEB_CHAT_IP_DAILY_LIMIT` mensajes por IP en 24 h (30). Abrir sesiones nuevas no lo reinicia.
  - `WEB_CHAT_DAILY_LIMIT` mensajes en total en 24 h (300). Es el tope que protege la cuota diaria de Gemini aunque lleguen muchas IP distintas.
- **Uso en la base** (`web_usage`, migración 0011): una fila por mensaje respondido. La IP se guarda como hash con sal (`ENCRYPTION_KEY`), alcanza para contar sin guardarla. Si el agente falla, no se registra.
- **IP del cliente configurable** (`WEB_CHAT_CLIENT_IP`):
  - `direct` es el par TCP. Sirve en la PC con cloudflared, porque uvicorn ya aplica `X-Forwarded-For` cuando viene de localhost.
  - También acepta un encabezado, con `:first` o `:last` para las listas.
  - En Render: `x-forwarded-for:first`. Según el personal de Render, la primera IP es la del cliente, pero hay reportes de que Render conserva lo que manda el cliente. Se verifica después del despliegue (docs/render.md).
- **Página:** el límite de la sesión cierra esa conversación. Los diarios avisan sin bloquearla, porque vencen solos.

## Pendiente

- Limitar también la creación de sesiones por IP. Hoy son filas baratas que no gastan cuota.
- Borrar periódicamente `web_usage` y `web_sessions` viejas.
- Verificar en Render qué IP llega y cerrar la duda del encabezado.
