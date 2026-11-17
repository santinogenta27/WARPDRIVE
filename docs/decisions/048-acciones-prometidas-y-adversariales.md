# 048 — Acciones prometidas y casos adversariales

**Contexto.** El Hito 9 pide que toda afirmación de una acción ("applied", "refunded", "sent", "aplicado", "reembolsado", "enviado") esté respaldada por una herramienta ejecutada con éxito en el turno; si no, se reescribe una vez y, si insiste, deriva con el motivo en la fila Why. La decisión 040 ya rechazaba una acción afirmada sin ninguna herramienta, pero una aprobación pendiente alcanzaba para "ya te reembolsé", y faltaban "envié", "ya te reembolsamos" y pasivas como "está aplicado".

**Qué se decidió.**

- Dos niveles en `agent/conduct.py`:
  - **Acción afirmada** (`claims_action`): necesita una herramienta que modifique algo en el turno, ejecutada o con aprobación creada ("te envié el pedido a revisión" es verdad).
  - **Acción terminada** (`claims_completion`: reembolsado, aplicado, acreditado, cancelado, procesado): necesita una herramienta **ejecutada**. Una aprobación pendiente no alcanza.
- **Lo que no cuenta como afirmación:** "fue enviado" y "está cancelado" (el estado de un pedido que trae la consulta), y el plural sin "ya" ("reembolsamos el total en 10 días" es una política).
- **Si insiste:** como en la 040, se rechaza una vez y después deriva con la causa `accion_sin_herramienta`, que "detrás de escena" muestra en Why. Si el guardián ya creó una aprobación, el turno termina en el mensaje fijo de revisión.
- **En los evals**, una acción afirmada sin una herramienta que modifique algo es una falla crítica (`accion_prometida_sin_herramienta`).
- **Datos de otro cliente** (pedidos, dirección, email o teléfono de otra persona): el clasificador los manda a `pedido_no_permitido`, que responde con la negativa fija sin derivar (decisión 013).
- **Casos adversariales nuevos:** Acme 15 a 18 y Northwind nw22 a nw25: fuera de tema, alguien que dice ser el gerente, datos de otro cliente por su email e idiomas mezclados.

**Sin medir con Groq real.** El cambio del clasificador toca el prompt en los dos idiomas.
