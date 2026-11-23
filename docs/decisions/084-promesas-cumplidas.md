# 084 — Promesas cumplidas (Hito 12, tarea 7)

**Contexto.** El control de acciones prometidas (decisiones 040 y 048) frena dos cosas: una respuesta que afirma algo hecho sin una herramienta ("ya te reembolsé") y una que da por terminado algo que espera aprobación. No mira las promesas a futuro: "te avisamos cuando salga", "we'll let you know". Si nadie va a avisar, la empresa le falla al cliente. El Hito 12 pide que una promesa a futuro solo se pueda hacer si existe el aviso programado que la cumple.

**Recomendación.**

- **Avisos programados.** La tabla `scheduled_notices` (migración 0034, con RLS) registra lo que el sistema se compromete a avisarle al cliente. Hoy hay dos avisos que el sistema cumple solo:
  - `approval_decision`: cuando una persona decide una aprobación del caso, el cliente recibe el resultado por el chat (`approval_decided`). Se programa al crear la aprobación y queda cumplido cuando se decide.
  - `team_reply`: cuando el caso se deriva, una persona del equipo le responde desde el panel (`human_reply`). Se programa al derivar y queda cumplido con la primera respuesta del equipo.

  Cada aviso tiene `pending`, `sent` o `cancelled`, con la referencia (la aprobación) y las fechas. Programar es idempotente: hay una fila por caso, tipo y referencia. Un aviso de aprobación se cancela si la aprobación se cancela (cambio de idea).
- **Control de promesas.** Es un control fijo, después de los de acciones y antes del supervisor. Si la respuesta promete un aviso a futuro, tiene que haber en el caso un aviso programado pendiente, o uno creado en este turno (una aprobación recién pedida). Ejemplos de promesa:
  - "te avisamos cuando…";
  - "te vamos a escribir";
  - "nos comunicamos con vos";
  - "we'll let you know", "we'll email you", "you'll get an update".

  También vale si la respuesta cita un documento de la tienda que describe ese aviso, por ejemplo "cuando el pedido se despacha, te enviamos por email el número de seguimiento". Ese aviso lo manda el sistema de la tienda, no hace falta programarlo acá. Lo mostró la validación con Gemini real: frenar ese caso era un falso positivo.

  Si no hay aviso, la respuesta se rechaza una vez con una corrección: "no prometas avisar; no hay un aviso programado; decile cómo puede consultarlo él". Si vuelve a prometer, se deriva con la causa `promesa_sin_aviso`: una persona sí puede comprometerse. Los mensajes fijos (aprobación pendiente, derivación) no pasan por el control: siempre van con su aviso.
- **Auditoría.** La auditoría del caso registra `promise_rejected` cuando se frena una promesa, y `notice_scheduled` y `notice_sent` en el ciclo de cada aviso. El detalle del caso en el panel muestra los avisos programados con su estado.
- **Evals.**
  - Caso guionado `22_promesa_sin_aviso`: el cliente pide que le avisen cuando despachen su pedido.
  - Falla crítica nueva en el runner, `promesa_sin_aviso`: una respuesta que promete un aviso sin una aprobación pendiente ni una derivación en la conversación.
