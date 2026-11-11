# 009 — Control de fundamento y derivación

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Contexto

La spec pide que toda afirmación factual tenga fuente o salga de una herramienta; si no, "se reescribe o se deriva". No dice cómo detectarlo.

## Decisiones

- **Controles en código** (`app/agent/grounding.py`), sin llamar a otro modelo:
  1. Las fuentes citadas tienen que estar entre los documentos que recibió el modelo.
  2. Una respuesta marcada `factual` necesita al menos una fuente o una herramienta usada en el turno.
  3. **Cada número** de la respuesta (fecha, monto, plazo, número de pedido) tiene que aparecer en la evidencia: documentos recibidos, resultados de herramientas o el mensaje del cliente. Atrapa datos inventados aunque el modelo no se declare factual.
- **Reescritura única:** si falla, se le devuelve la respuesta rechazada con el motivo y tiene un intento más. Si vuelve a fallar, se deriva con el motivo en el resumen. La respuesta rechazada nunca llega al cliente ni al historial.
- **Resumen de derivación** en `cases.escalation`: intención, mensaje del cliente, si el contacto está verificado, pasos hechos con herramientas y motivo. El caso queda `escalated`.

## Límites conocidos

- No mide la **relevancia** de la fuente citada ni afirmaciones sin números ("sí, hacemos arreglos" citando un documento real pero que no habla de eso). Eso lo mide el juez de evals (Opus, tarea 11). Si los evals con claves reales muestran que pasa seguido, el siguiente paso es un verificador con Haiku antes de enviar.
- Un número que figura en algún documento recibido pasa aunque se use fuera de contexto ("48 horas" aparece en envíos y facturación).
