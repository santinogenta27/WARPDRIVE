# 061 — Ventas cruzadas con reglas

**Contexto.** El punto 3 del Hito 10 pide que la empresa elija en el panel qué productos ofrecer, cuándo y con qué descuento máximo, y que lo aplique el guardián: como máximo una oferta por conversación y nunca si el cliente está enojado o en medio de un reclamo.

**Qué se decidió.**

- **Regla (`upsell_rules`, con `tenant_id` y RLS):**
  - el producto, con el nombre que tiene en la tienda;
  - los tipos de caso donde se ofrece ("cuándo");
  - el descuento máximo, de 0 a 50 %.
  - Se carga en Configuración → Ventas cruzadas.
- **Un reclamo no vende.** Un tipo de caso es un reclamo si usa una herramienta que modifica un pedido del cliente (reembolso, cancelación, cambio, cupón por un problema), es decir, si escribe con control de dueño. El panel no deja elegirlo, y el guardián lo vuelve a controlar.
- **La herramienta interna `ofrecer_producto`** (como `cancelar_pendiente`) se ofrece al modelo solo si se cumplen tres cosas: hay una regla activa para el tipo de caso, todavía no hubo una oferta en la conversación y es buen momento. Su descripción lista los productos con su descuento máximo.
- **El guardián la ejecuta y controla todo en código, aunque el modelo la llame igual:**
  1. hay una regla activa para ese producto y el tipo de caso del caso;
  2. el descuento no pasa el máximo (el rechazo trae `maximo_permitido`);
  3. es buen momento:
     - ningún mensaje del cliente en el caso es de enojo (en inglés o en español);
     - el caso no espera una aprobación ni está derivado;
     - el tipo de caso no es un reclamo;
  4. no hubo otra oferta en la conversación. `upsell_offers` es única por caso, así que dos llamadas a la vez no hacen dos ofertas;
  5. el producto está en la tienda con stock (precio y stock salen de `GET /products`).
- **El descuento llega al link de compra solo desde la oferta.** `POST /checkouts` acepta `discount_percent`. El guardián lo deja pasar solo si la conversación tiene una oferta con al menos ese porcentaje y todos los SKU del carrito son del producto ofrecido. Si no, se rechaza y el link se arma sin descuento.
  - Northwind agrega `?discount=NW15OFF` y descuenta el total.
  - Shopify crea un código de un solo uso y lo aplica con `?discount=` en el permalink.
  - Tiendanube crea el cupón y lo devuelve como `discount_code`, para que el cliente lo escriba al pagar, porque la página del producto no aplica cupones.
- **Northwind** ofrece el Wool Beanie en `shopping`, hasta 15 %.

**Consecuencia.** Un carrito con descuento tiene solo el producto ofrecido. Si el cliente quiere también otra cosa, el asistente arma dos links. Así el código de Shopify, que vale para todo el carrito, no descuenta nada más.

**Pendiente.** Probar con una tienda real que el código de un solo uso se aplique desde el permalink.
