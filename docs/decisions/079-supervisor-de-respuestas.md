# 079 — Supervisor de respuestas (Hito 12, tarea 2)

**Contexto.** Los controles fijos en código atrapan lo que tiene forma reconocible:

- formato;
- números y fechas sin respaldo;
- acciones afirmadas sin herramienta;
- cobros;
- empatía.

No ven si la respuesta contradice una política del documento, si interpreta mal el resultado de una herramienta o si rompe el tono de la marca. El Hito 12 pide que un modelo barato revise cada respuesta antes de enviarla. La spec no define cuándo corre, con qué modelo ni qué pasa si falla.

**Recomendación.**

- **Cuándo corre.** Después de los controles fijos y antes de enviar, en cada respuesta final (`responder`) que:
  - es factual;
  - cita fuentes;
  - o sale de un turno en el que corrió una herramienta (datos o acciones).

  No corre en la charla (saludos, fuera de tema), en los mensajes fijos (verificación, negativa, cierre, aprobación pendiente) ni en las derivaciones.
- **Modelo.** El del clasificador (`claude-haiku-5-5`; con Gemini o Groq, su modelo chico). Pasa por `LLMClient` y su costo queda en `usage`.
- **Qué recibe**, todo como dato y nunca como instrucción:
  - el mensaje del cliente;
  - la respuesta propuesta;
  - los documentos que citó;
  - los resultados de las herramientas del turno;
  - las reglas del tipo de caso;
  - el tono de la marca.
- **Qué devuelve.** Usa la herramienta `supervisar` y elige una de tres opciones:
  - `aprobar`;
  - `reescribir`, con el motivo;
  - `derivar`, con el motivo.
- **Qué se hace con eso.**
  - `aprobar`: se envía.
  - `reescribir`: el motivo vuelve al agente como corrección, una sola vez, y la respuesta nueva pasa otra vez por los controles fijos y por el supervisor. Si vuelve a pedir reescribir, se deriva.
  - `derivar`: se deriva con la causa `supervisor` (decisión 045). Si en el turno ya se creó una aprobación, queda el mensaje fijo de aprobación pendiente, como en todo `_give_up`.
- **Si el supervisor falla** (cuota, error, respuesta sin la herramienta), se aprueba y se registra `supervisor_unavailable`. La respuesta ya pasó los controles fijos, y frenar todo el servicio por una revisión extra sería peor. Se puede ver en la auditoría.
- **Medición.** Cada revisión deja `supervisor_review` en la auditoría con el veredicto, el motivo, los milisegundos, los tokens y el costo. La llamada al modelo queda con el papel `supervisor` en los pasos del turno (decisión 044). "Behind the scenes" muestra el veredicto con su latencia y su costo. Configuración muestra, de los últimos 30 días, cuántas revisiones hubo, cuántas pidieron una reescritura o una derivación, la latencia media que agrega y el costo total.
- **Por empresa.** El supervisor viene encendido (`settings.answer_supervisor`, por defecto `true`) y se apaga en Configuración → Supervisor de respuestas. No es el modo supervisado de la decisión 072, que deja cada respuesta como borrador para una persona.

**Alternativa descartada.** Correrlo antes de los controles fijos: gastaría una llamada en respuestas que el código igual iba a rechazar sin costo.
