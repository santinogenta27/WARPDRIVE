# 090 — Qué hace el agente en pausa

**Contexto.** En Home y Deploy, "Pause agent" pide confirmar con "Customers will be told that a
person will reply". El QA de configuración (Hito QA 2, etapa 1) encontró que no era así: pausar
solo sacaba el widget de los dominios de la empresa. Por WhatsApp y en la página propia del chat
el agente seguía respondiendo con el modelo, y en el sitio el cliente que estaba conversando se
quedaba sin chat.

**Opciones.**
1. Dejarlo como estaba y cambiar el texto del panel ("the chat disappears from your site").
2. En pausa, el chat sigue en el sitio y en todos los canales, pero cada mensaje se deriva a una
   persona, sin llamar al modelo, con el mensaje fijo de derivación. Es el mismo camino que el
   tope de gasto al 100 % (decisión 049).

**Recomendación (aplicada): 2.** Es lo que promete el panel y lo que espera una empresa que pausa
porque algo anda mal: que el agente deje de contestar y que su equipo atienda desde Inbox.

- "En pausa" es: salió en vivo alguna vez (`settings.live` existe) y ahora está en `false`. Antes
  de salir en vivo no hay pausa: el playground y la página de prueba siguen atendiendo.
- El widget sigue cargando en los dominios de la empresa mientras está en pausa.
- La auditoría registra la derivación con la causa `agente_en_pausa`, y el equipo recibe el aviso
  de caso derivado como siempre.
- Las empresas de demo están siempre en vivo y no se pueden pausar.
