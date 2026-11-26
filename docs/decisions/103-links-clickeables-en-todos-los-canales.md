# 103 — Links que se pueden tocar en todos los canales

## Contexto

En la prueba gratis con deathwishcoffee.com el agente armó bien el carrito, pero el link salió como
texto crudo: `http://localhost:8000/demo/prueba-…/cart/DEMO-P006-STA-OS:1?ref=ea-…`. El `format()`
del widget solo convertía `*negritas*` y saltos de línea, así que ningún link del chat se podía
tocar, para ningún cliente. En WhatsApp el link se mandaba con `preview_url: false` y en el medio
de la frase. Y en la prueba el carrito llevaba a la página de la demo, no a la tienda del visitante.

## Decisión

### Chat web (widget y página `/c/…`)

- El texto de cada mensaje se arma con nodos (`createTextNode`, `textContent`, `setAttribute`):
  ya no hay `innerHTML` con texto del modelo. Solo `http` y `https` se vuelven links; `javascript:`
  o `data:` quedan como texto. A una URL suelta no se le pega la puntuación final.
- Links en Markdown `[texto](url)` y sueltos. Todos abren en otra pestaña con
  `rel="noopener noreferrer"`.
- Un link de compra (ruta con `/cart` o `/checkout(s)`) sale del texto y es un botón debajo:
  "Complete your purchase · $7.00 →" / "Completá tu compra · $7.00 →". El total viene de
  `checkout_links` (lo anota el guardián, decisión 063), no del texto del modelo; sin total, el
  botón va sin monto. El producto no se muestra: `checkout_links` no lo guarda y sacarlo del texto
  sería adivinar.
- Cualquier otro link muestra el dominio y "↗" (la URL entera queda en el `title`). Un link en
  Markdown a un dominio que no es de la empresa ni de la plataforma muestra también el dominio, y
  un botón de compra a otro dominio lo muestra adentro.
- `/chat/widget/config` manda `link_hosts` (plataforma, dominios del chat, del sitio y de la
  prueba) y `purchase_note` (solo en una prueba con tienda real). Los eventos `message` y
  `/chat/updates` mandan `checkouts` con el total de cada link de compra.
- El widget pasa de ~42 KB a ~48 KB: el test de tamaño sube de 45 KB a 50 KB.

### WhatsApp (Meta y Twilio)

- Los links salen del texto (un Markdown deja su texto) y van, cada uno en su línea, después de una
  línea en blanco. `WhatsAppClient.send_text` pide `preview_url: true` cuando el cuerpo trae una URL.
- El link va corto: `{PUBLIC_BASE_URL}/go/{código}`, en la tabla `short_links` (migración 0038,
  aditiva, con `tenant_id` y RLS). El código son 12 caracteres al azar de 62. Única por
  `(tenant_id, target)`: el mismo destino (que ya lleva el `ref`) da el mismo código.
- Solo se acortan destinos de un dominio permitido de la empresa (plataforma, chat —que incluye la
  tienda conectada, decisión 086—, sitio o prueba). Uno ajeno se manda entero.
- Se aplica al enviar, no al guardar: el historial del caso conserva el texto del agente. Pasa por
  el handler de Meta, `handle_inbound` (Twilio), `reply_as_human` y la entrega de un borrador
  aprobado por Twilio (`supervision.deliver`, una línea).

### `/go/{código}`

- Busca el código (fuera de un tenant, como el ruteo de `channels`) y vuelve a confirmar que el
  destino sea de un dominio permitido de esa empresa. Nunca usa una URL de la dirección: no es una
  redirección abierta. Código inexistente o con otra forma: página 404 amable, en inglés y
  castellano. Redirige con 302, `Cache-Control: no-store` y `Referrer-Policy: no-referrer`.

### Prueba gratis

- Shopify (`/products.json`): cada producto guarda `variant_id` (la primera variante disponible) y
  `variant_ids` (por opción). La API simulada de la prueba sigue armando el carrito con los SKU de
  ejemplo, y `_Sandbox` cambia el link por el permalink real
  `https://{dominio de la tienda}/cart/{variant_id}:{cantidad}?ref=…`: abre el checkout de la tienda
  con el producto; no crea nada ni cobra.
- Otra plataforma: la página real del producto (con el `ref`). La prueba de ejemplo, sin tienda,
  sigue con la página de la demo. Los pedidos DEMO-100x no cambian.
- Debajo del botón: "This opens your real store's checkout. In your pilot, every sale the agent
  starts is tracked in your dashboard." (en castellano en `/probar`).

### presentable.py

- El chequeo de "nombre de herramienta" no mira dentro de las URLs: un slug o `utm_source` con
  guiones bajos ya no bloquea la respuesta.

## Opciones descartadas

- **Mostrar el producto en el botón sacándolo del texto:** frágil y podría mostrar otro producto.
- **Botones interactivos de WhatsApp (`interactive` con `cta_url`):** cambian el tipo de mensaje y
  las reglas de la ventana de 24 h; el pedido era un mensaje común con vista previa.
- **Acortar cualquier dominio:** convertiría `/go/` en un acortador abierto para phishing.

## Pendiente

- La compra en la tienda real de una prueba no se puede atribuir (no hay API de la tienda): en el
  piloto, con la tienda conectada, sí.
