# 059 — Productos con precio, stock y variantes reales

**Contexto.** El Hito 10 suma ventas al mismo agente. El punto 1 pide una herramienta para buscar productos con precio, stock y variantes reales (catálogo CSV indexado o conector de Shopify/Tiendanube), responder preguntas de preventa con fuente, y que el precio y el stock salgan siempre de una herramienta, nunca del modelo.

**Opciones.**

1. Catálogo CSV indexado en el RAG. El precio y el stock serían texto de un documento: pueden quedar viejos, y el control de fundamento no distingue un precio del catálogo de un número de una política.
2. Una operación más en la API canónica de tienda (decisión 057): `GET /products`, que implementan la demo y los conectores.

**Recomendación (la elegida): 2.**

- **La API canónica suma `GET /products?query=&size=`.** Devuelve cada producto con su precio, su moneda y sus variantes (talle, color, SKU) con el stock de ese momento. `stock: null` es una tienda que no lleva stock (siempre disponible).
- **Northwind** la implementa con su catálogo (`mocks/northwind-api/data/products.json`, ahora con stock por color y talle; los SKU son los mismos de los pedidos).
- **Shopify:** `products.json` (activos, hasta 250) filtrados por las palabras de la búsqueda, porque la API REST filtra por título exacto. El talle y el color salen de las opciones con esos nombres. El stock es `inventory_quantity`, nunca negativo, si la tienda lo lleva. La moneda sale de `shop.json`.
- **Tiendanube:** `products?q=` filtrado por palabras de la misma forma. Los textos vienen por idioma, el talle y el color salen de `attributes`, y `stock: null` es stock sin límite. La moneda sale de `store`.
- **La herramienta `buscar_productos`** sale del OpenAPI por el mismo generador: riesgo bajo, sin pedir identidad (es preventa) y por el guardián como todas.
- **Northwind suma el tipo de caso `shopping`** con esa herramienta.

**Precio y stock solo de una herramienta (en código).** En un turno que ofrece la búsqueda de productos, antes del control de fundamento de siempre:

- Cada monto de la respuesta (`$149`, `85.000 pesos`, `20 USD`) tiene que estar en un resultado de herramienta del turno, o en una política. Un documento cuyo id parece una lista de precios (`catalog`, `productos`, `precios`) no respalda un precio, porque puede estar viejo. Tampoco lo respalda el mensaje del cliente ("¿sale $99?").
- Decir que algo está o no está en stock exige una herramienta ejecutada en el turno.
- Si no pasa, se le pide al modelo que corrija una vez (como el control de fundamento) y, si insiste, deriva. Detrás de escena lo muestra como "precio" o "stock".

**Pendiente.** Una empresa sin Shopify ni Tiendanube necesita `GET /products` en su propia API (la conexión por OpenAPI ya lo toma). El catálogo por CSV como sistema de productos queda para después. Los conectores se probaron con respuestas de ejemplo de la documentación: falta probarlos con una tienda real.
