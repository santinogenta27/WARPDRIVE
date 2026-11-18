# 060 — Link de compra: el agente arma el carrito y nunca cobra

**Contexto.** El punto 2 del Hito 10 pide armar el carrito y devolver el link de checkout de la tienda (permalink de Shopify o su equivalente en Tiendanube). El agente nunca cobra.

**Opciones.**

1. Crear un pedido o un checkout en la plataforma por API (Shopify Draft Orders, Storefront Cart). Exige más permisos, deja pedidos a medio hacer en la tienda y está más cerca de cobrar.
2. Un link que abre el checkout de la tienda con el carrito armado, sin crear nada.

**Recomendación (la elegida): 2.**

- **La API canónica suma `POST /checkouts`** con `items: [{sku, quantity}]`. Responde `url`, las líneas con su precio, `total` y `currency`. Rechaza un SKU que no existe o sin stock suficiente (409). No cobra, no reserva stock y no crea nada.
- **Northwind** arma `https://shop.northwind.example/cart/SKU:cantidad,...`. La demo puede apuntarlo a su propia página (`checkout_base`).
- **Shopify:** el permalink de carrito `https://{tienda}/cart/{id de variante}:{cantidad},...`, que abre el checkout con el carrito cargado. Solo lee productos para traducir el SKU a la variante y validar el stock.
- **Tiendanube:** su API no arma carritos. El equivalente es la página del producto con la variante elegida (`canonical_url?variant={id}`), donde el cliente toca "Comprar". Por eso es un producto por link (con más de uno responde 409). Falta verificarlo con una tienda real.
- **La herramienta `armar_carrito`** sale del OpenAPI: riesgo bajo, sin pedir identidad, sin monto (no mueve dinero) y por el guardián. Northwind la suma al tipo de caso `shopping`.

**Nunca cobra (en código).**

- Ninguna herramienta cobra ni compra por el cliente. Por eso, decir "te cobré", "your order has been placed" o "I've charged your card" se rechaza siempre: el modelo corrige una vez y, si insiste, deriva (causa `cobro_afirmado`).
- En un turno de ventas, cada link de la respuesta tiene que estar en un resultado de herramienta o en un documento. Un link inventado se rechaza (control `link`).
