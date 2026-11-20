# 075 — Prueba gratis self-service con la tienda del visitante

**Contexto.** Un visitante tiene que poder probar, con su propia tienda, el mismo agente que
después compra: sus políticas leídas de su sitio, sus productos y un flujo completo (verificación,
pedido con seguimiento, oferta, descuento negado, recomendación con link de compra y un reembolso
que él mismo aprueba). Sin tocar su tienda real, con límites de costo y abuso, y con un camino
directo a piloto. La spec no define cómo se arma la empresa de prueba ni dónde viven sus pedidos.

## Opciones para los pedidos de ejemplo

1. **Pedirle acceso a su plataforma (Shopify, Tiendanube).** Pedidos reales, pero toca su tienda
   y exige permisos antes de probar nada. Descartada.
2. **Modo simulado desde un OpenAPI** (`SandboxRuntime`). Respuestas armadas, sin estado: el
   reembolso aprobado no cambia nada y no hay seguimiento coherente.
3. **La API simulada de Northwind, con su catálogo y pedidos `DEMO-…`** (recomendada). Ya tiene
   pedidos con demora, entregados y sin despachar, cupones, reembolsos idempotentes y links de
   compra. Se le pasan el catálogo, los clientes y el prefijo de los pedidos.

## Recomendación

- **Página pública `/probar`** (`/try` o `?lang=en` en inglés): URL de la tienda y email. Se
  manda un código de 6 dígitos (vence en 15 minutos, 5 intentos). El enlace de la prueba
  (`/probar/{token}`) lleva un token al azar: solo se guarda su hash.
- **Empresa de prueba.** Al confirmar el código se crea un tenant normal (con RLS como cualquier
  otra) llamado `Prueba · {dominio}`, con `settings.trial`, "detrás de escena" prendido y su chat
  web. Un job (`trial_build`, en el worker del onboarding) hace el resto y guarda el avance:
  1. Rastrea el sitio con el rastreo de siempre (`crawl`, mismo dominio, 30 páginas) y lo indexa
     como el panel (se redactan tarjetas y documentos). Cada página queda como fuente citable.
  2. Lee los productos del HTML de esas mismas páginas: datos estructurados `schema.org/Product`
     (JSON-LD, los publican Shopify, Tiendanube y WooCommerce) y, si no hay, las etiquetas
     `og:` de producto. Nombre, precio, moneda, imagen y link; hasta 12. Sin modelo: es gratis y
     determinístico. Si no encuentra ninguno, usa tres productos genéricos y la página lo dice.
  3. Configura herramientas y tipos de caso como Northwind (mismo OpenAPI, mismo generador),
     contra su API simulada `demo:trial:{tenant}`.
  El job es idempotente: cada paso reemplaza lo suyo (páginas por fuente, productos, tools con
  `on conflict`), así que correrlo dos veces no duplica nada.
- **Pedidos de ejemplo.** `DEMO-1001` en camino con demora, `DEMO-1002` entregado (para el
  reembolso) y `DEMO-1003` sin despachar, con sus productos y fechas relativas a hoy. El cliente
  de esos pedidos es el propio visitante, con su email: verificarse le manda el código a su
  casilla. La página los muestra como "pedidos de ejemplo". La API vive en memoria por proceso y
  se rearma desde la fila de la prueba (un reinicio vuelve los pedidos a su estado inicial).
- **Reglas (en código, por el guardián):** cupón hasta 10 % (un descuento mayor se niega y ofrece
  envío express), todo reembolso pide aprobación (`approval_over: 0`, independiente de la
  moneda). Tipos de caso: dónde está mi pedido, reembolso, compras y cambio de dirección. Las
  instrucciones son las de Northwind salvo el reembolso, que no menciona montos ni plazos de
  Northwind. Las instrucciones quedan en inglés también en una prueba en castellano: el agente
  contesta en el idioma de la empresa.
- **Mini panel.** La página de la prueba muestra el chat con "detrás de escena" (`/demo/{slug}`),
  los pedidos de ejemplo, sugerencias de mensajes y las aprobaciones pendientes, que el visitante
  aprueba o rechaza ahí mismo con el mismo `decide` del panel (y el aviso al cliente en el chat).
- **Límites:**
  - Una prueba por email y una por dominio, para siempre (índices únicos). La fila de la prueba
    queda después de borrar la empresa: así el límite sigue y el operador ve el historial.
  - 30 mensajes por prueba, sumando todas las sesiones del chat (`trials.messages`).
  - Tope de costo: `monthly_budget_usd` de la empresa en `TRIAL_COST_CAP_USD` (0,50 por
    defecto). Si se pasa, el chat responde el aviso de límite de la prueba sin llamar al modelo.
  - Bots: campo trampa oculto, un mínimo de 3 segundos entre abrir y enviar el formulario (con la
    hora firmada), como mucho 3 pruebas por IP por día y la confirmación del email.
  - A los 7 días, si no pasó a piloto, se borra la empresa con todos sus datos (el mismo borrado
    del operador, que deja registro). Es un job `trial_expire` por prueba, encolado al crearla con
    clave de idempotencia: correrlo dos veces no hace nada la segunda.
- **"Empezar mi piloto"** lleva a `/registro?prueba={token}` con el nombre de la tienda y el email
  cargados (el email no se cambia). Al confirmar el email, en vez de crear una empresa nueva se
  convierte la de prueba: toma el nombre elegido, crea el usuario admin, se saca la marca de
  prueba, el tope de la prueba y "detrás de escena", se desconecta la API de ejemplo (sus
  herramientas y tipos de caso quedan desactivados) y se archivan las conversaciones de prueba.
  Quedan las páginas del sitio y el documento "Catálogo" con sus productos.
- **"Hablar con Santino"** manda un email al operador (`PANEL_EMAIL`) con el sitio y el email del
  visitante. Una sola vez por prueba.
- **Panel del operador:** "Pruebas" (`/panel/pruebas`) con sitio, email, estado, mensajes, si
  pidió el piloto, si se convirtió y si pidió hablar.
- **Datos personales:** la tabla `trials` es de la plataforma (como `signups`): no tiene RLS ni
  acceso para `app_user`. Guarda el email porque hace falta para el código, el seguimiento y el
  límite por email. El contenido del sitio es público y pasa por la misma redacción que el panel.
