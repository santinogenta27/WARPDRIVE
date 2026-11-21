# 076 — Seguridad de red: solo direcciones públicas por HTTPS (SSRF)

**Contexto.** Varias funciones hacen pedidos HTTP a direcciones que carga un usuario:
- el rastreo del sitio (onboarding del panel y prueba gratis);
- la URL base del sistema de la empresa (OpenAPI);
- los conectores de Shopify y Tiendanube.

Antes se verificaba el DNS al guardar o al empezar, pero httpx volvía a resolver el dominio al conectar. Un DNS que cambia entre la verificación y la conexión (DNS rebinding) podía llevar el pedido a la red interna o a los metadatos de la nube (169.254.169.254). Además, `fetch_url` seguía redirecciones sin verificarlas, el rastreo aceptaba `http://` y ninguna respuesta tenía tope de tamaño.

## Opciones

1. **Verificar antes y confiar en la conexión.** Es lo que había. Deja abierto el DNS rebinding y las redirecciones.
2. **Un proxy de salida aparte con lista de destinos.** Es lo más fuerte, pero en el plan gratis de Render no hay dónde correrlo.
3. **Un transporte de httpx que verifica y fija la IP** (recomendada). Cada pedido, incluida cada redirección, resuelve el DNS, exige que todas las direcciones sean públicas y conecta a esa IP. El dominio va en `Host` y en el SNI, así el certificado se sigue validando contra el dominio.

## Recomendación

`app/net.py`:
- **`SafeTransport`:**
  - solo HTTPS;
  - bloquea loopback, redes privadas, CGNAT, link-local (incluida la de metadatos), reservadas, multicast, `::1`, `fc00::/7`, `fe80::/10` y las IPv4 dentro de IPv6 (`::ffff:127.0.0.1`);
  - bloquea nombres internos (`localhost`, `.local`, `.internal`, sin punto) sin resolverlos;
  - un dominio con alguna IP privada se bloquea entero;
  - tope de tamaño de la respuesta: 5 MB por defecto y 2 MB por página del sitio, controlado por la cabecera y mientras se lee;
  - timeout de 15 s y 5 s para conectar.
- **`safe_client()`:** el cliente para URLs de usuarios.
- **`check_url()`:** la misma validación, para avisar antes. `/probar` rechaza una dirección interna antes de guardar la prueba.

**Dónde se usa:**
- `crawl` y `fetch_url`: cada redirección pasa por el transporte. Una página interna que falla no corta el rastreo.
- `http_client` del sistema de la empresa.
- Shopify, el intercambio de tokens de Shopify y Tiendanube, como transporte de salida por defecto.

**Webhooks:** los del producto son solo de entrada: Meta y Twilio nos llaman a nosotros. Las salidas a WhatsApp, Twilio, Resend y los modelos van a dominios fijos del proveedor, no a una URL cargada por un usuario.

**Desarrollo:** `ALLOW_PRIVATE_SYSTEM_URLS=1` sigue apagando el control, solo para probar contra una API en localhost. Mantiene el tope de tamaño.
