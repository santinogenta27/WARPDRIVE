# 022 — Panel mínimo de la empresa para la demo (Hito 5)

**Estado:** adoptada (10 de noviembre de 2026). Revisar cuando llegue el panel completo de la spec.

## Contexto

La demo tiene que mostrar el lado de la empresa, no solo el chat:

- aprobar o rechazar los reembolsos pendientes;
- atender las conversaciones derivadas;
- ver la auditoría de cada caso;
- mostrar el reporte de preparación del onboarding.

La spec prevé un panel en Next.js con magic link y roles por empresa. Para la demo tiene que entrar en el servicio gratis de Render que ya sirve el chat: un solo proceso y sin build de frontend.

## Decisiones

- **Páginas servidas por la misma API** (`app/panel/`):
  - Son HTML armado en el servidor con la biblioteca estándar (`html.escape`), sin Jinja ni JavaScript.
  - El CSS va dentro de la página, sin recursos externos, con modo claro y oscuro.
  - Todo texto que viene de la base o del cliente pasa por `esc`.
  - Rutas:
    - `/panel/casos`: la bandeja.
    - `/panel/casos/{id}`: el detalle del caso.
    - `/panel/aprobaciones`: los pedidos pendientes de aprobación.
    - `/panel/reporte`: el reporte de preparación.
- **Login de demo**:
  - Un solo usuario, configurado en el entorno con `PANEL_EMAIL` y `PANEL_PASSWORD`. Sin esas variables, nadie entra.
  - La sesión es una cookie firmada con HMAC. La firma usa `PANEL_SECRET` o, si no está, `ENCRYPTION_KEY`.
  - La cookie vence a las 12 horas y es `HttpOnly`, `SameSite=Lax`, `Secure` bajo HTTPS y limitada a `/panel`.
  - Cada formulario que cambia algo lleva un token anti-CSRF derivado de la sesión. Sin ese token, la respuesta es 403.
  - En la tabla `users` se registra un usuario `owner` por email, para que `approvals.decided_by` diga quién decidió.
  - **No es el login de la spec.** El magic link y los roles quedan para el panel completo.
- **Formularios sin dependencias nuevas**: el cuerpo `x-www-form-urlencoded` se lee con `urllib.parse`. No hace falta `python-multipart`.
- **Aprobaciones**:
  - Se usa el mismo `approvals.decide`: el guardián ejecuta lo aprobado y la fila queda bloqueada, así no se ejecuta dos veces.
  - Después de decidir, `notify_customer` deja un mensaje `human` en el caso, con la decisión y el pedido, y registra en la auditoría el evento `approval_decided` con quién decidió.
  - El chat y el panel comparten la API simulada de Acme (`shared_acme`): lo que se aprueba se ve después en el pedido que consulta el agente.
- **Bandeja**:
  - Orden: derivados, esperando aprobación, abiertos, resueltos; dentro de cada estado, por última actividad.
  - Muestra el último mensaje del cliente, el tipo de caso, el canal y si el contacto está verificado.
- **Respuesta como humano**: el texto se guarda como mensaje `human` y queda en la auditoría como `human_reply`.
- **Mensajes del equipo en el chat web**:
  - La página consulta `GET /chat/updates` cada 6 segundos mientras la pestaña está visible.
  - Para el servicio gratis de Render se eligió **polling y no SSE abierto**: no deja conexiones colgadas y es una consulta indexada por sesión.
  - Solo devuelve mensajes del contacto de esa sesión.
  - Por WhatsApp, el envío de estas respuestas queda pendiente mientras el canal está en pausa (`outbound.reply_as_human` ya existe).
- **Detalle de un caso**:
  - La auditoría reconstruida paso a paso, en castellano: mensaje, clasificación, búsqueda con sus fuentes, herramientas con la decisión del guardián, respuesta con las fuentes citadas, derivación y respuestas humanas.
  - El costo por llamada y el total en USD, y el resultado de `verify_chain`, es decir, si la cadena de hashes está íntegra.
- **Reporte de preparación**:
  - Lee el último `onboarding_runs` terminado del tenant "Acme Store (onboarding)".
  - Muestra el porcentaje que resolvería solo, ponderado por volumen.
  - Por tipo: volumen, porcentaje resuelto y qué falta, con los casos que fallaron desplegables.
  - También los adversariales frenados.

## Pendiente

- El panel completo de la spec: Next.js, magic link, roles y varias empresas.
- Activar tipos y herramientas desde el panel.
- Enviar por WhatsApp las respuestas del panel cuando el canal vuelva.
- Cerrar un caso desde el panel.
- Limitar los intentos de login. Hoy es un solo usuario de demo con una clave larga.
