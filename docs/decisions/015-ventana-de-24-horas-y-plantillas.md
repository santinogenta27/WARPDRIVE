# 015 — Ventana de 24 h y plantillas

**Estado:** adoptada (tarea 12d). Revisar.

## Contexto

WhatsApp acepta mensajes libres solo hasta 24 h después del último mensaje del cliente; fuera de esa ventana solo acepta plantillas aprobadas. La spec pide usar `caso_actualizado` cuando un humano responde fuera de la ventana y mandar el mensaje pendiente cuando el cliente responde. Para escribirle a un cliente horas después hace falta su número, y hasta ahora el contacto solo tenía el hash.

## Decisiones

- **Dirección cifrada:** `contacts.wa_address_enc` guarda el wa_id y el nombre de perfil, cifrados con Fernet y `ENCRYPTION_KEY`. Se agrega la dependencia `cryptography`, que también hace falta para cifrar el token de cada canal (tarea 12e). El hash sigue siendo la identidad del contacto y el número en claro nunca se guarda.
- **Ventana:** se mide desde `contacts.last_customer_message_at`, que se actualiza con la hora que manda Meta en cada mensaje entrante.
- **`reply_as_human(conn, tenant, case, texto)`** (`app/channels/whatsapp/outbound.py`):
  - Dentro de la ventana envía el texto libre.
  - Fuera de la ventana, o si Meta rechaza con 131047, envía la plantilla `caso_actualizado` con el nombre de perfil como `{{1}}` ("cliente" si no hay nombre) y deja el texto en `outbound_messages`.
  - El mensaje del humano queda en `case_messages` con rol `human` y en la auditoría.
- **Cuando el cliente vuelve a escribir:** primero se envían sus mensajes pendientes. Si había alguno, ese mensaje no pasa por el agente, porque casi siempre es un "hola" para abrir la ventana. Si el agente lo procesara, terminaría derivando otra vez.
- **Idioma de la plantilla:** `WHATSAPP_TEMPLATE_LANGUAGE`, `es_AR` por defecto. La plantilla tiene que estar creada y aprobada en Meta (ver docs/whatsapp.md).

## Pendiente

- La ruta `POST /cases/{id}/reply` necesita la autenticación del panel (hito 5). La lógica ya está en `reply_as_human`.
- Plantilla `aprobacion_resuelta` cuando se decide una aprobación fuera de la ventana.
