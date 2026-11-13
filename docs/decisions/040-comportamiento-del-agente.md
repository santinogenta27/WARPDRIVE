# 040 — Comportamiento del agente: empatía, opciones, cambio de idea y dos temas

**Estado:** adoptada (13 de noviembre de 2026). Hito 8, tarea 5. Decidida sin aprobación previa: revisala.

## Contexto

La tarea pide cuatro conductas: empatía antes de resolver si el cliente está molesto, ofrecer opciones cuando hay más de una salida válida, manejar un cambio de idea (cancelar lo pendiente antes de hacer lo nuevo) y atender dos temas en un mismo mensaje.

Probando el guion de la demo en Chrome con gpt-oss-20b aparecieron tres fallas más:

1. "OK, I'll take the free express shipping" se clasificó como consulta general.
2. En ese flujo, sin herramientas, el modelo respondió "I've added free express shipping to your next order" sin haber hecho nada. Es una respuesta inventada.
3. "Actually, can you make it a 25% coupon instead?" se clasificó como "otro" y el caso se derivó.

El criterio es el de siempre: lo que importa se controla en código; el prompt ayuda, no garantiza.

## Decisiones

### 1. El clasificador sigue el tema de la conversación

- Recibe `<tipo_actual>`, el tipo de caso que la conversación tiene hasta ahora. La regla: si el mensaje la continúa, mantiene ese tipo salvo que el cliente traiga claramente otro tema. Continuar es una respuesta corta, elegir una opción, pedir algo más sobre el mismo pedido o cambiar de idea.
- Vale en los dos idiomas. En el primer mensaje de un caso no hay tipo actual y nada cambia.

### 2. Dos temas en un mensaje

- El clasificador puede devolver `otro_tipo`, un segundo tipo de caso activo.
- El agente recibe las instrucciones y las herramientas de los dos tipos, consulta cada pedido y responde todo en un solo mensaje.
- El caso queda con el tipo principal, y la auditoría registra el segundo tema (`second_topic`).

### 3. Cambio de idea: el guardián exige cancelar lo pendiente

- **La regla, en código:** si el caso tiene una solicitud pendiente de aprobación sobre un pedido, una acción con otra herramienta sobre ese mismo pedido se rechaza hasta cancelarla. El rechazo dice qué está pendiente.
- **La misma herramienta** sigue como antes: reusa la aprobación pendiente del caso, aunque cambie el monto (`test_guardian.py`).
- **`cancelar_pendiente`** es una herramienta interna que el agente recibe solo si el caso tiene algo pendiente. La ejecuta el guardián (`Guardian.cancel_pending`), que solo toca las aprobaciones de ese caso:
  - las deja rechazadas, así nadie las ejecuta después;
  - el caso vuelve a abierto.
- **Sin migración:** el estado `rejected` ya existía y una cancelación del cliente queda así. En la auditoría y en el panel se ve "Cancelada por el cliente".

### 4. Controles de conducta sobre la respuesta (`app/agent/conduct.py`)

- **Acción afirmada sin herramienta.** Si la respuesta dice que hizo algo ("I've added", "has been activated", "ya te agregué") y ninguna herramienta modificó nada ni pidió aprobación en el turno, se rechaza con una corrección. Si se repite, deriva. Vale en los dos idiomas. Lo que el texto presenta como hecho antes ("already", "earlier") no cuenta.
- **Empatía.** Si el mensaje del cliente muestra enojo ("ridiculous", "frustrating", "still hasn't arrived", "!!") y la respuesta no lo reconoce ("sorry", "understand"), se pide una vez que empiece por ahí. Si igual no lo hace, la respuesta sale: el tono no justifica derivar. Por ahora es solo en inglés, porque el agente en español mide contra su línea base de antes.

### 5. Opciones

- **En el prompt en inglés:** cuando hay más de una salida válida, se presentan las opciones y se deja elegir, sin aplicar ninguna hasta que el cliente decida.
- **En el tipo de caso:** el pedido atrasado ya lo pedía ("let the customer choose").
- **Por qué no en código:** controlarlo en código exigiría saber cuáles son las opciones de cada política. El eval lo mide.
- **El objetivo de `where_is_my_order` cambió:** ahora nombra la compensación de un atraso. Así un pedido de cupón o de envío express sobre un pedido va a ese tipo, con sus herramientas, y pasa por el guardián.

### 6. Las reglas de conducta van en un bloque aparte, solo en inglés

- `system_v5_en.txt` sigue siendo la traducción exacta de `system_v5.txt` (decisión 036).
- Las reglas nuevas están en `prompts/conduct_v1_en.txt`: dos temas, empatía, opciones, cambio de idea y no afirmar acciones. Se suman al prompt de las empresas en inglés.
- El historial de evals registra la versión como `system_v5_en+conduct_v1_en`.
- **El prompt en español no cambia:** así la línea base de Acme sigue valiendo. Pasar estas reglas al español es traducir el bloque y volver a medir Acme.

### 7. Evals en inglés

Cinco casos nuevos:

| Caso | Qué mide |
|---|---|
| nw17 | cliente molesto: empatía antes del estado del pedido |
| nw18 | pedido atrasado: ofrece cupón o envío express y deja elegir |
| nw19 | cambio de idea: cancela el reembolso pendiente y hace el cambio de talle |
| nw20 | dos temas: estado de un pedido y cambio de talle de otro |
| nw21 | el guion completo de la demo, con verificación por código |

El runner suma dos controles, que se aplican solo a los casos que los definen (los de Acme no cambian):

- **`texto_incluye`:** grupos de palabras; de cada grupo, la respuesta tiene que traer al menos una.
- **`ejecutadas_en_la_conversacion`:** herramientas que se ejecutaron en cualquier turno.

Además, antes de cada caso la API simulada de Northwind vuelve a sus pedidos iniciales, y al cerrar un caso se rechazan sus aprobaciones pendientes. La API de Acme no tiene ese reinicio: sus evals corren igual que antes.

## Límites

- **El control de empatía mira palabras.** Una disculpa sin esas palabras puede pedir una corrección de más. Es una sola, y después la respuesta sale igual.
- **Las acciones afirmadas también se detectan por patrones.** Un resumen de algo hecho en otro turno, sin "already" ni "earlier", puede rechazarse y terminar derivado.
- **Dos temas, uno con cada tipo:** con tres temas, el tercero no tiene sus herramientas.
