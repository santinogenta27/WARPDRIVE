# 101 — La prueba gratis lee el catálogo de cualquier tienda y lleva a hablar con el equipo

**Contexto.** Con https://www.allbirds.com la prueba encontró un solo producto: leía el JSON-LD de
cada página, tomaba cada talle como un producto ("… - Size 8", "… - Size 8.5") y el agente dijo
"We currently offer the Men's Runner NZ Slip On…" como si fuera lo único que vendía. Se presentó
como "Prueba · allbirds.com" (el nombre interno del tenant) y la sugerencia repetía el nombre largo.
La prueba es lo primero que ve un cliente potencial: tiene que funcionar con cualquier sitio y
llevarlo a hablar con el equipo o a pedir el piloto.

**Opciones para el catálogo.**
1. Seguir leyendo solo el HTML de las páginas que rastrea el armado (lo de hoy): pocos productos y
   variantes sueltas.
2. Un navegador completo (Playwright) en el servidor: lee todo, pero es demasiado pesado para Render.
3. Las fuentes públicas de cada plataforma, en orden, y quedarse con la primera que dé productos.

**Recomendación (aplicada): 3**, en `app/trial/sources.py`:

| Orden | Fuente (`source` en el informe) | Cómo |
|---|---|---|
| 1 | Shopify (`shopify_json`) | `/products.json?limit=250&page=N` hasta una página con menos de 250 o el tope. Nombre y moneda de `/meta.json`. |
| 2 | WooCommerce (`woocommerce`) | `/wp-json/wc/store/v1/products?per_page=100&page=N`, precios en unidades menores (`currency_minor_unit`). |
| 3 | Squarespace (`squarespace`) | `?format=json` en la página de la tienda (`/shop`, `/store`, la ruta que pegó el visitante o un link de la portada). |
| 4 | Datos estructurados (`structured_data`) | URLs de producto del `sitemap.xml` (los que declara `robots.txt`, los índices y sus hijos, también `.gz`). De cada página: JSON-LD `Product`, `ProductGroup` con `hasVariant`, `Offer` y `AggregateOffer`; después microdata; después Open Graph de producto. |
| 5 | Modelo validado (`llm_validado`) | Solo si no hubo datos estructurados: el modelo de la prueba (`TRIAL_GEMINI_API_KEY`, por `LLMClient`) lee el texto de hasta 20 páginas de producto. Un producto se acepta solo si su nombre y el precio tal como lo devolvió aparecen literalmente en esa página. |

- **Sin dependencias nuevas.** Para JSON-LD, microdata y Open Graph alcanza con `html.parser` de la
  biblioteca estándar, como ya hacía `catalog`. `extruct` sumaría `lxml` y otras cuatro dependencias
  a la imagen de Render por tres formatos que ya se leen en unas cien líneas.
- **Un producto, no sus variantes.** Las variantes de talle y color se agrupan en un producto con su
  rango de precios y las variantes disponibles. En Shopify, las variantes vienen en `variants[]`; los
  productos que comparten el nombre antes del último " - " (Allbirds publica un producto por color)
  se agrupan en uno. Precio, imagen y link son los de un mismo producto real (el más barato con
  stock), así siempre coinciden con su página.
- **Completos o afuera.** Cada producto tiene nombre limpio, precio y moneda (y precio de oferta si lo
  hay), imagen, link, categoría, descripción corta y stock cuando la fuente lo dice. Se descartan, y
  se cuentan por motivo: sin nombre o sin precio, tarjetas de regalo, productos de prueba, ocultos o
  sin publicar, bundles vacíos y duplicados.
- **Muchos productos.** Hasta 150 por prueba. Shopify, WooCommerce y Squarespace los dan en pocos
  pedidos, durante el armado. Por sitemap, el armado lee 10 páginas de producto y el resto se suma en
  segundo plano (`phase: rest`, el mismo job de la decisión 099); el agente los usa apenas quedan
  indexados.
- **Buenos modales.** Todo por `net.safe_client` (SSRF), con timeout, dentro de los 60 s del armado.
  Se respeta `robots.txt`, se espacian los pedidos al mismo sitio (4 por segundo como máximo) y el
  User-Agent dice quiénes somos. `/llms.txt`, si está, se indexa como una página más.
- **Las políticas.** En Shopify, además de las del pie, `/policies/shipping-policy`, `refund-policy`,
  `privacy-policy` y `terms-of-service`.
- **Informe de calidad** en `trials.progress.quality`: fuente usada, productos por fuente, encontrados,
  descartados por motivo, políticas encontradas, llamadas y tokens del modelo, y segundos. Va en el
  email de aviso al operador.

**Que nunca falle feo.** Cada falla tiene su código y su mensaje: `not_found` (el dominio no existe),
`unreachable` (caído), `slow` (no respondió a tiempo), `blocked` (403, 429 o el desafío de
Cloudflare), `password` (Shopify con contraseña), `js_only` (el sitio se arma con JavaScript y no
tiene sitemap ni datos estructurados). En `blocked`, `js_only` y `password`, la página ofrece seguir
con una **prueba de ejemplo** (productos y políticas de ejemplo, con el nombre de la tienda) o hablar
con el equipo por WhatsApp. Un sitio que bloquea la portada pero publica su catálogo (lo vimos con
WooCommerce) sigue adelante con el catálogo.

**Un sitio que no es tienda** (servicios, una clínica, un SaaS) se arma igual, en modo `services`: sus
páginas y servicios, sin pedidos de ejemplo ni productos inventados, y sugerencias sobre servicios.

**El agente.** `TrialAgent` reemplaza el nombre del tenant ("Prueba · dominio") por el de la tienda,
el mismo del encabezado del chat, y contesta en el idioma en que escribe el visitante (si el mensaje
no lo deja claro, el de sus mensajes anteriores; si no, el de la prueba). Las instrucciones del tipo
de caso de compras le piden no decir nunca que la tienda vende "solo" lo que encontró.

**Ventas.**
- "Hablar con Santino" pasa a "Hablá con nuestro equipo por WhatsApp" / "Talk to our team on
  WhatsApp". Abre `wa.me` con un mensaje en el idioma de la prueba que nombra la tienda; antes pasa
  por `/probar/{token}/whatsapp`, que anota el contacto y avisa al operador como antes.
- Al operador le llega un email cuando termina un armado (lista o falla), una sola vez por prueba:
  tienda, email, idioma, productos, link e informe de calidad.
- Después de 5 mensajes, o cuando se acaban los 30, la página muestra un resumen con datos reales
  de la prueba (consultas resueltas, derivadas y acciones del guardián) y los botones del piloto y de
  WhatsApp.

**El aviso de velocidad y el "Typing…" sin tocar el widget.** El chat de la prueba es el widget de
siempre dentro de un iframe del mismo origen (`/demo/{slug}`). Para no cambiar el widget de todas
las empresas, la página de la prueba mira el `shadowRoot` del widget: cuando aparece su indicador
(`.typing .label`), le pone "Typing…" y, pasados 8 segundos, agrega la nota del modelo compartido. Un
test verifica que esas clases sigan existiendo en `widget.js`. Si el widget cambia, el chat sigue
funcionando; solo se pierde la nota.

**Lo que encontró el QA con tiendas reales** (Allbirds, Death Wish Coffee, JOCO Cups en
WooCommerce, Le Pou en Tiendanube y Basecamp), ya resuelto:

- La Store API de WooCommerce devuelve en `price` el precio con impuestos (60,45 = 54,95 + 10 %);
  la ficha muestra el de lista o el de oferta. Se usan `regular_price` y `sale_price`.
- El sitemap de Tiendanube lista las URLs sin `www` y Cloudflare responde 403 a esa variante: las
  URLs del sitemap se leen en el mismo host que la portada.
- Algunas tiendas cortan con 403 o 429 una ráfaga de pedidos: 4 por segundo como máximo,
  compartidos entre el rastreo y el catálogo, y ante un 429 una sola espera corta.
- Un 429 de embeddings durante el armado (Gemini lo informa como cuota diaria) dejaba la demo
  "ocupada" 5 minutos, justo cuando el visitante empieza a chatear. El embedder del armado ya no la
  bloquea: sigue por texto y completa los vectores después. Solo una cuota agotada en el chat la
  bloquea, como decía la decisión 100.
- La búsqueda de la tienda de ejemplo devolvía decenas de productos ("men" y "wool" están en casi
  todo Allbirds) y el resultado le llegaba cortado al agente, que terminaba derivando. La tienda de
  ejemplo de la prueba devuelve los 8 que mejor coinciden y cuántos más hay.
- La cola de jobs tiene un solo hilo: un armado de 15 s esperó 58 s detrás del segundo plano de
  otra prueba. El segundo plano trabaja de a tramos de 10 s y encola el resto después de lo que esté
  en cola.
- En un sitio que no es tienda, el agente ofrecía pedidos y productos: en modo `services` los tipos
  de caso de pedidos, reembolsos y compras quedan apagados.

**Pendiente, fuera de `app/trial/`.** Con varios visitantes seguidos, un armado todavía puede
esperar hasta un tramo del segundo plano de otra prueba (en el QA, hasta 25 s con cinco pruebas
seguidas). Un hilo propio para los armados en `app/companies/worker.py` lo resolvería del todo.

**Lo que queda afuera.** Configurar APIs o reglas en la prueba: conectar la tienda real es parte del
piloto. Un sitio que solo funciona con JavaScript y no publica sitemap ni datos estructurados no se
puede leer sin navegador: se le explica al visitante y se le ofrece la prueba de ejemplo.
