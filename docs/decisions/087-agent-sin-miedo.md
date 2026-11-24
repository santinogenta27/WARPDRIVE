# 087 — Agent: configurar sin miedo

**Contexto.** Las opciones del agente estaban en una página larga, con explicaciones técnicas ("el supervisor de respuestas", "el prompt"). Antes de tocar algo, el cliente no sabía qué hacía ni cómo iba a quedar, y al guardar solo aparecía una tarjeta verde arriba de todo.

## Qué se decidió

- **Una pestaña por tema:** Rules, Actions, Topics, Brand & voice, Sales y Language (decisión 085).
- **Cada opción, con ayuda y ejemplo.** `agent_forms.field` arma cada opción con:
  - su nombre;
  - el control;
  - una línea de qué hace, en lenguaje simple;
  - un ejemplo ("Example: Mon to Fri, 9:00 to 18:00").

  Los interruptores (Review before sending, Answer check, cada acción y cada tema) siguen la misma forma, con su estado y un botón Turn on/Turn off. El test de aceptación falla si un control queda sin su explicación.
- **Marca y voz con una vista previa compartida.** Brand & voice muestra el chat real de la empresa al lado de los dos formularios. Se actualiza al escribir, sin guardar:
  - nombre, color, logo, saludo y preguntas sugeridas;
  - una respuesta de ejemplo armada con la voz: trato en español (vos, tú o usted), emojis y firma.

  El widget suma la opción `sample` a `EnterpriseChat.setBrand`, que solo usa la vista previa del panel. Los cambios que llegan antes de que cargue la configuración del chat se guardan y se aplican al cargar.
- **"Saved".** Al guardar, en todo el panel, un aviso flotante (`role="status"`) se va solo, y recargar no lo vuelve a mostrar. Los errores siguen como aviso fijo en la página.
- **Sin jerga técnica en Actions.** Se sacó el método y la ruta de la API de cada acción. Quedan su nombre, su descripción y sus hechos: riesgo, identidad, si modifica datos y tope.
- Los formularios envían a los mismos endpoints de antes (`/panel/configuracion/...`): no cambia la lógica ni los controles.
