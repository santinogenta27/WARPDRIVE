# 082 — Asistente para el equipo humano (Hito 12, tarea 5)

**Contexto.** Cuando un caso llega al equipo (derivado, o con un borrador del modo supervisado), la persona tiene que leer toda la conversación y la auditoría para entender qué pasó y qué contestar. El Hito 12 pide mostrarle cuatro cosas en el caso:

- un resumen;
- la memoria del cliente;
- el paso siguiente sugerido;
- una respuesta sugerida, que la persona edita y envía.

La spec no define cuándo se genera, con qué modelo ni qué se guarda.

**Recomendación.**

- **Dónde.** Una tarjeta "Asistente para el equipo" arriba del detalle del caso, solo si el caso está derivado o tiene un borrador pendiente. En los demás casos no aparece y no se llama a ningún modelo.
- **Cuándo se genera.** Al abrir el caso, si todavía no hay sugerencia o si llegaron mensajes nuevos desde la última. Queda guardada en `case_assists` (migración 0032, con RLS, una fila por caso), así volver a abrir el caso no gasta otra llamada. El botón "Volver a sugerir" la genera de nuevo.
- **Modelo.** El del agente (`claude-sonnet-5-5`), por `LLMClient`, con la herramienta `asistir_equipo`. Esa herramienta devuelve el resumen, el paso siguiente y la respuesta sugerida. El modelo recibe, como dato y nunca como instrucción:
  - la conversación (los últimos 20 mensajes);
  - el motivo de la derivación;
  - las herramientas que corrieron con su resultado;
  - las reglas del tipo de caso;
  - la memoria del cliente;
  - la voz de marca.

  El uso queda en `usage` con el caso. La auditoría del caso suma `assist_generated`, con el modelo, los tokens y el costo.
- **Memoria del cliente.** Si el contacto del caso se verificó alguna vez, se muestra su memoria (decisión 078), con un enlace a la página del cliente. El equipo tiene permiso de verla.
- **Respuesta sugerida.** Viene en un campo de texto editable, dentro del mismo formulario "Responder como persona del equipo" de siempre. No sale nada sin que una persona toque "Enviar". La respuesta enviada queda en la auditoría como `human_reply` con `sugerida: true` cuando salió de ese formulario. La envía una persona, así que no pasa por el guardián ni por el supervisor.
- **Sin modelo** (sin claves, con la cuota agotada o si falla): la tarjeta se arma igual en código.
  - El resumen sale del motivo y de la intención de la derivación.
  - El paso siguiente es el genérico ("revisá el motivo y respondé").
  - No hay respuesta sugerida.

  Ese resultado no se guarda: se vuelve a intentar al abrir el caso.
