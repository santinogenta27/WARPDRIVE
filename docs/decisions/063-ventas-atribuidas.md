# 063 — Ventas atribuidas al agente

**Contexto.** El punto 5 del Hito 10 pide un código o link con un identificador por conversación y, en Métricas, "ventas atribuidas al agente" (cantidad y monto).

**Opciones.**

1. Un cupón por conversación. Cambia el precio, se comparte y no sirve si el cliente ya tiene otro cupón.
2. El identificador en el link de compra, y que la tienda diga después qué compra salió de ese link.

**Recomendación (la elegida): 2.**

- **El identificador.** Es `ea-` más 12 caracteres del id del caso (`conversation_ref`), sin datos del cliente.
- **Lo pone el código.** En el OpenAPI canónico, el campo `ref` del cuerpo de `POST /checkouts` está marcado `x-conversation-ref`. El generador lo saca de la herramienta (el modelo no lo ve ni lo elige) y anota `conversation_ref` en el manifiesto. El guardián lo completa con el caso al llamar a la API.
- **Cada link armado queda anotado** en `checkout_links` (migración 0025, `tenant_id` y RLS), con el caso, el total y la moneda.
- **La compra la informa la tienda.** La API canónica suma `GET /checkouts/{ref}`, que responde `open` o `completed` con el pedido y el monto.
  - **Northwind** guarda cada checkout por `ref`. La demo simula el pago con `POST /checkouts/{ref}/complete`, que es solo de la API simulada.
  - **Shopify:** el permalink lleva `attributes[asistente_ref]=…`, que queda en `note_attributes` del pedido. La consulta busca ese atributo entre los pedidos de los últimos 60 días.
  - **Tiendanube:** su link es la página del producto y no puede llevar el identificador hasta el pedido. Por ahora sus ventas no se atribuyen (la consulta responde 404).
- **Sincronizar es idempotente.** Métricas → **Actualizar ventas** pregunta por las conversaciones con links sin compra de los últimos 60 días, a través del guardián (solo lectura). Una compra se anota una sola vez, en el último link de su conversación.
- **Métricas** muestra la cantidad de ventas, el monto por moneda, los links armados y qué parte termina en compra.

**Pendiente.**

- Sincronizar sola, con un job periódico o con el webhook `orders/create` de Shopify.
- Atribuir ventas en Tiendanube (por ejemplo, con un cupón por conversación).
