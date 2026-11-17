# 057 — Conectores de Shopify y Tiendanube

**Contexto.** El Hito 9 pide acciones reales sin programar: un asistente para conectar la API de la empresa con OpenAPI (credenciales cifradas y prueba de cada herramienta) y un conector listo para Shopify (pedidos, envíos, reembolsos y cupones), y Tiendanube si es razonable.

**Qué ya estaba.** La conexión por OpenAPI con URL base, credenciales cifradas por empresa, prueba de conexión y activación (decisión 026).

**Qué se decidió.**

- **Una API canónica de tienda.** Es la de la demo de Northwind: `GET /orders/{order_id}`, `POST /orders/{order_id}/refunds`, `POST /orders/{order_id}/coupons` y `GET /customers?email=`. Cada conector es un transporte de httpx que traduce esa API a la de la plataforma y la respuesta de vuelta.
  - Así las herramientas salen del OpenAPI canónico por el mismo generador, con los mismos controles del guardián (identidad, dueño, montos y aprobación), sin tocar el guardián.
- **Shopify** (Admin REST API `2025-01`, con un token de una app propia de la tienda):
  - pedido por su número (`name`), con estado, productos, total, dirección, transportista y seguimiento;
  - reembolso sobre la transacción cobrada, por el monto pedido;
  - cupón: una regla de precio de un solo uso con su código.
- **Tiendanube** (API `v1`, con el token de una app): pedido, envío y cupón. **El reembolso no:** su API no reembolsa. Responde que lo hace una persona; como el reembolso ya pide aprobación, quien aprueba lo hace en Tiendanube.
- **Dueño del pedido:** el teléfono del cliente o, si no tiene, su id en la plataforma. El mismo valor sale en la búsqueda por email y en el pedido, así el control de dueño del guardián compara lo mismo.
- **Probar cada herramienta:** Configuración → Sistema → **Probar herramientas** corre cada herramienta de solo lectura con un pedido de ejemplo que carga la empresa y muestra qué respondió. Las que modifican algo no se prueban contra el sistema real: se prueban contra la copia simulada (sandbox), como en el reporte.
- **Sin probar contra una tienda real:** los tests usan respuestas de ejemplo de la documentación de cada API. Hay que probarlo con una tienda de desarrollo de Shopify (gratis) antes del primer cliente.
