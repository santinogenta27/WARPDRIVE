# 033 — CSAT y alertas por email

**Estado:** adoptada (12 de noviembre de 2026). Hito 7, tarea 3.

## Contexto

La decisión 028 dejó dos pendientes: el CSAT, que la spec nombra entre las métricas, y un destino para las alertas, que solo quedaban en los logs. Desde la decisión 029 hay un `Mailer` real.

## CSAT

### 1. La pregunta va en el mensaje de cierre

Cuando el cliente cierra el caso ("gracias", "eso era todo"), el asistente responde:

> ¡De nada! Si necesitás algo más, escribime cuando quieras.
>
> ¿Pudimos resolver tu consulta? Respondé **sí** o **no**. Si querés, contanos por qué.

- **Es texto, no un botón.** Así funciona igual en la página del chat, en el widget y en WhatsApp, sin tocar ningún canal.
- **Una sola vez por caso.** Si el caso se reabre y se cierra de nuevo, no vuelve a preguntar.
- **No pregunta en el cierre por 24 h sin respuesta:** el cliente ya no está en la conversación.

Opción descartada: botones Sí/No en la página del chat. Se ven mejor, pero no existirían en WhatsApp y obligarían a mantener dos caminos.

### 2. La respuesta

- El mensaje siguiente del cliente es una respuesta si empieza con "sí" o "no" (o 👍 / 👎). Lo que sigue es el comentario.
- **"si compro hoy, ¿cuándo llega?" no es una respuesta:** sin tilde y sin un signo después, es otra consulta.
- Si el cliente escribe otra cosa, sigue el flujo normal y la pregunta queda sin responder.
- La pregunta vence a las 24 h.
- La respuesta no abre un caso nuevo: queda en el caso que se cerró, con su evento `csat_answered` en la auditoría.

### 3. Un "no" reabre el caso y lo pasa a una persona

- El caso vuelve a estar abierto, queda marcado como reabierto y se deriva con el motivo "El cliente respondió que su consulta no se resolvió."
- El cliente recibe: "Gracias por avisarnos, y disculpá." y el aviso de que lo atiende una persona.
- **Ese caso ya no se cobra** (regla de reapertura, decisión 027). Si el cliente dice que no se resolvió, no es un caso resuelto por el asistente.

Opción descartada: solo anotar el "no". Es más simple, pero deja cobrando un caso que el propio cliente dijo que no se resolvió, y nadie lo atiende.

### 4. Dónde se ve

- **Métricas → Satisfacción (CSAT):** porcentaje que dijo que sí, cuántos respondieron de los preguntados y los últimos 10 comentarios, con enlace al caso.
- **Detalle del caso:** un paso "Satisfacción" en la auditoría.
- Los comentarios son texto del cliente. Se ven solo en el panel de su empresa, con los códigos de verificación tapados.

### 5. Datos

Tabla nueva `csat_responses` (migración 0018), con RLS: caso, cuándo se preguntó, respuesta, comentario y cuándo respondió.

## Alertas por email

### 1. Qué se envía y a quién

- Las tres alertas de la decisión 028: latencia, costo por caso y errores.
- **Siguen quedando en los logs**, igual que antes. El email es un aviso más.
- **Destinatario:** `ALERT_EMAIL_TO`. Si no está, `PANEL_EMAIL` (el operador).
- Salen por el `Mailer`. Sin `RESEND_API_KEY` y `MAIL_FROM`, la alerta queda solo en los logs: no se duplica con el Mailer de consola.

### 2. Cuidados

- **No demora la respuesta al cliente:** el email sale en otro hilo.
- **No inunda la casilla:** como mucho un email por tipo de alerta y empresa cada 15 minutos (`ALERT_EMAIL_INTERVAL_S`). El siguiente dice cuántas alertas iguales no se enviaron.
- **Si el envío falla**, queda una línea en los logs y nada más: avisar no puede romper un turno.
- **Sin datos personales:** el email lleva la misma línea del log, con ids y números.

### 3. Límites conocidos

- El control de frecuencia vive en memoria, como el contador de errores: alcanza para un solo proceso.
- La demo usa `PANEL_EMAIL` con un dominio de prueba (`@acme.example`), al que no se envían emails (decisión 029). Para recibir alertas reales hay que cargar `ALERT_EMAIL_TO`.

## Pendiente

- Botones Sí/No en el widget, además del texto.
- CSAT para los casos que se cierran solos a las 24 h.
- Alerta por caída de la tasa de resolución o del CSAT.
- Un resumen diario en lugar de un email por alerta.
