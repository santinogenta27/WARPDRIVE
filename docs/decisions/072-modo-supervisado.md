# 072 — Modo supervisado (Hito 11, tarea 1)

**Contexto.** En los pilotos, una empresa quiere ver cada respuesta del asistente antes de que
llegue al cliente. La spec no lo define.

**Opciones.**
1. Retener la respuesta en el canal: el agente corre igual (clasificación, guardián, herramientas,
   auditoría) y lo que iba a enviarse queda como borrador.
2. Frenar el agente antes de las herramientas: el borrador no podría decir el resultado de una
   consulta, y aprobar obligaría a correr el agente de nuevo.

**Recomendación: la 1.**

- **Opción por empresa.** `tenants.settings.supervised`. Apagado por defecto. Se cambia en
  Configuración, "Modo supervisado".
- **Dónde se retiene.** En el canal, después del agente: `handle_inbound` (chat web y Twilio) y el
  handler de WhatsApp. La respuesta del agente sale del historial del caso y queda en
  `reply_drafts`. Así el modelo no la ve en el turno siguiente si nunca llegó al cliente.
- **Las herramientas no cambian.** Pasan por el guardián como siempre, con sus aprobaciones. El
  borrador es solo el texto.
- **En la bandeja.** Casos muestra arriba los borradores por revisar, y el detalle del caso
  muestra los suyos. Cada uno tiene el texto editable y dos botones:
  - **Enviar:** manda el texto tal cual o editado, y queda en el historial como respuesta del
    asistente.
  - **Rechazar:** no se envía nada. La persona puede responder con el formulario de siempre.
  - Lo puede hacer cualquier persona del equipo, no solo los admins.
- **Mientras tanto, el cliente:**
  - **Chat web:** el stream termina con el evento `held`. El widget muestra "Escribiendo…" hasta
    que `/chat/updates` trae la respuesta. `updates` informa `typing` mientras haya un borrador
    pendiente y devuelve también las respuestas aprobadas.
  - **WhatsApp (Meta):** el indicador de "escribiendo" de la Cloud API, con el mensaje marcado
    como leído. Meta lo muestra hasta 25 s o hasta la respuesta. Si falla, no se reintenta.
  - **Twilio:** no tiene indicador.
- **Entrega al aprobar.**
  - Web: por `updates`.
  - WhatsApp: `outbound.reply_as_human`, con la ventana de 24 h y la plantilla.
  - Twilio: el canal configurado en el entorno.
  - Si el envío falla, la transacción se revierte y el borrador sigue pendiente.
- **Auditoría.** Se registran tres eventos:
  - `draft_held`: el texto y el canal.
  - `draft_sent`: el texto final, si se editó y quién lo envió.
  - `draft_rejected`: quién lo rechazó.
- **Un borrador se decide una sola vez.** Un doble clic no lo envía dos veces.
