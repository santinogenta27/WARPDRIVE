# 045 — Derivar con los datos ya en la mano

**Estado:** aplicada (16 de noviembre de 2026).

## Contexto

En la demo en inglés de Render (Northwind, 16/11 a las 14:55), la clienta:

1. preguntó por NW-4521;
2. mandó el email;
3. escribió el código.

El agente consultó el pedido y la herramienta funcionó: los 5 controles del guardián en verde, "Ran in the store's system". Igual, la respuesta fue "I'm passing you to a member of our team". Eso bloqueaba la grabación.

**Qué dice la auditoría del caso** (`audit_events`, caso `e67137cd`):

| Evento | Qué pasó |
|---|---|
| `tool_call` | `consultar_pedido` ejecutada, con el pedido completo en el resultado. |
| `model_call` | gpt-oss-20b, 91 tokens de salida. |
| `escalated` | Motivo: "Customer request for order status". |

No hay `grounding_rejected`, `format_rejected` ni `tone_rejected`, y el control de VERIFICATION_TALK no intervino. **Ningún control rechazó una respuesta: el modelo eligió derivar.**

**La reproducción:** se repitió contra Groq el mismo pedido del agente (prompt, documentos, historial y resultado de la herramienta). En 2 de 2 respondió bien: estado, demora en Denver, 14 de noviembre y las dos opciones. Es una elección ocasional del modelo chico, no un error determinista.

También se descartó que la causa fuera que la nueva fecha de entrega (14/11) ya pasó: con los mismos datos respondió bien.

## Opciones

1. **Solo el prompt:** más reglas sobre cuándo derivar. El prompt ya dice "derivar solo si…", y gpt-oss-20b igual derivó. Baja la frecuencia, pero no la elimina.
2. **Un control en código, como los de conducta (decisión 040):** si el agente deriva en un turno en el que una herramienta ya devolvió datos, se le pide una vez que responda con esos datos.
3. **Prohibir derivar después de una herramienta.** Le quita al agente una salida legítima, por ejemplo un problema que las herramientas no resuelven. Relaja la seguridad.

## Decisión

La opción 2, en `app/agent/loop.py`.

**Cuándo se aplica:** el agente llama a `derivar` y, en el mismo turno:

- una herramienta se ejecutó sin error;
- no hay una aprobación creada (de eso se ocupa `_approval_pending`).

**Qué hace:**

- Se registra `handoff_rejected` en la auditoría.
- Se le devuelve al modelo una corrección: responder con los datos y los documentos, y derivar solo si el cliente pidió una persona o si es un problema que sus herramientas no resuelven.
- Si vuelve a derivar, se deriva. El control no le quita la derivación.
- La respuesta nueva pasa por todos los controles de siempre: fundamento, formato, conducta y verificación.

**Qué no cambia:**

- No se relaja ningún control existente.
- La charla no deriva nunca, como antes.
- Pedir una persona o una amenaza legal siguen derivando antes del modelo.
- Una consulta que el guardián negó, o con un error, deriva como antes.

**Costo:** una llamada más al modelo, solo cuando pasa.

## El motivo en "Behind the scenes"

Cada derivación registra en `escalated` una `causa`: `{"tipo": ..., "detalle": ...}`. Los tipos posibles están en `HANDOFF_CAUSES`, de `loop.py`. Por ejemplo:

- `{"tipo": "fundamento", "detalle": "numero"}`
- `{"tipo": "agente", "reconsiderada": true}`

La vista la muestra en una fila **Why**, en una línea armada solo con textos fijos (`behind.handoff_line`). Ejemplos:

- "Answer blocked by grounding check: a number not in the documents or tool results"
- "The agent chose to hand off, even after being asked to answer with the tool's data"

**Sin datos del cliente:** el motivo libre que escribe el modelo y los valores rechazados (números, fechas, fuentes) no se muestran. El control nuevo aparece en **Checks** como "Reconsidered a handoff".

**Casos de antes de esta decisión:** no tienen `causa` y no muestran la fila.

## Cómo se prueba

- **Tests:** `backend/tests/test_derivacion_con_datos.py`.
- **Modelo simulado:** dos modos nuevos.
  - `deriva_tras_consultar`: con el pedido consultado, deriva hasta que se le pide responder. Es lo que hizo gpt-oss-20b.
  - `deriva_siempre_tras_consultar`: deriva siempre.
