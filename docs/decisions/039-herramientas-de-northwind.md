# 039 — Herramientas nuevas de Northwind: cambio, cupón y envío express

**Estado:** adoptada (13 de noviembre de 2026). Hito 8, tarea 4. Decidida sin aprobación previa: revisala.

## Contexto

La demo en inglés necesita que el agente resuelva más que consultar y reembolsar: cambiar un talle, compensar un pedido atrasado y negarse bien a un descuento fuera de la política. Las herramientas tienen que salir del OpenAPI, como cualquier herramienta, y las reglas aplicarse en código, en el guardián.

## Decisiones

### 1. Tres operaciones nuevas en la API simulada

Están en `mocks/northwind-api/openapi.json`. El nombre de la herramienta sale del `operationId`, como las de Acme.

| Herramienta | Operación | Qué hace |
|---|---|---|
| `iniciar_cambio` | `POST /orders/{id}/exchanges` | Cambia una prenda de un pedido entregado por otro talle, color o producto. Devuelve la diferencia de precio y la etiqueta de devolución. |
| `ofrecer_cupon` | `POST /orders/{id}/coupons` | Cupón de cortesía de un porcentaje para la próxima compra, válido 60 días. |
| `ofrecer_envio_express` | `POST /orders/{id}/express-shipping` | Envío express gratis en la próxima compra, válido 60 días. |

- **El catálogo vive en la API** (`data/products.json`), con precios, talles y colores. El cambio a otro producto calcula la diferencia: positiva la paga el cliente, negativa se le devuelve.
- **Cada prenda se cambia una sola vez.** Un pedido sin entregar, una prenda que no está en el pedido o un talle o color que no existe dan 409, con el motivo.
- **Una cortesía por pedido:** un cupón o el envío express, no los dos. La segunda da 409.

### 2. Los controles son los de siempre

Las tres actúan sobre un pedido del cliente. Por eso el generador les pone:

- contacto verificado;
- control de dueño;
- clave de idempotencia `case_id + order_id`.

El generador las marca de riesgo bajo: no mueven dinero de la empresa hacia afuera. La diferencia de un cambio la paga el cliente o se le devuelve dentro del mismo cambio.

### 3. El tope del 10 % lo aplica el guardián, con alternativa

La política dice "hasta 10 %" y, si el cliente pide más, ofrecer envío express gratis. Para eso:

- **`amount_from: input.percent`:** el guardián mide el porcentaje del cupón contra `max_amount: 10`. Es el mismo control de monto máximo que ya existía: sobre el tope se rechaza siempre, aun con aprobación.
- **`alternatives`, campo nuevo del manifiesto:** la lista de herramientas que la política permite en su lugar. Si está, el rechazo del guardián suma `maximo_permitido` y `alternativas`: la misma herramienta hasta el máximo y las otras. El agente ofrece eso, en lugar de solo negarse.
- **La API no ve el pedido fuera de política.** La API acepta de 1 a 100 %, porque el tope es de la política de la empresa, no del sistema.

La empresa fija estos límites al revisar la herramienta: `review.set_tool(..., alternatives=...)`. Regenerar las herramientas los conserva, igual que los demás límites. Las herramientas sin `alternatives`, como las de Acme, se rechazan igual que antes.

### 4. Cada tipo de caso ofrece las suyas

- **`exchange`:** `consultar_pedido` e `iniciar_cambio`.
- **`where_is_my_order`:** suma `ofrecer_cupon` y `ofrecer_envio_express`. Sus instrucciones dicen que la cortesía se da cuando el cliente la elige, una por pedido.
- **`refund`:** las instrucciones dicen que llame a `iniciar_reembolso` con cualquier monto. Probado en Chrome con gpt-oss-20b: con la redacción anterior ("los reembolsos de más de $100 necesitan la aprobación de una persona"), el modelo derivaba el caso él mismo en lugar de llamar a la herramienta, y no se creaba la aprobación.

### 5. Un segundo pedido atrasado

`NW-4538`, de Daniel Brooks, demorado en USPS. Los casos de evals comparten la API simulada, y con un solo pedido atrasado el cupón de un caso bloquearía el envío express del siguiente.

## Límites

- **Sin control de los 30 días.** La API no mira la fecha de entrega para el cambio: los pedidos de la demo tienen fechas fijas, que con el tiempo quedarían fuera de plazo. La política está en los documentos y el agente la cita.
- **Sin stock.** Todo talle y color del catálogo está disponible.
- **El panel no edita las alternativas.** Se fijan en `company.json` de la empresa de demo. El panel muestra el tope del cupón como porcentaje.
