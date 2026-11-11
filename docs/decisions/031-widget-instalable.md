# 031 — Widget instalable

**Estado:** adoptada (11 de noviembre de 2026). Hito 7, tarea 1.

## Contexto

Hasta acá cada empresa tenía su chat en una página propia (`/c/<enlace>`). Para vender hace falta que el chat viva en el sitio del cliente, con su marca, y que se instale sin ayuda. La spec no define un widget.

## Decisiones

### 1. Una línea de `<script>`

```html
<script src="https://SERVICIO/widget.js" data-chat="ENLACE" async></script>
```

- El panel muestra la línea lista para copiar, en **Configuración → Widget para tu sitio**.
- `widget.js` es un solo archivo, sin dependencias ni build: unos 30 KB sin minificar. Se carga con `async`, así que no frena la página.
- Opcionales: `data-theme="light|dark"`, `data-position="left"`.
- `window.EnterpriseChat.open()`, `.close()` y `.toggle()` permiten abrirlo desde un botón del sitio.

### 2. Mismo flujo que `/c/…`

El widget usa las rutas que ya existían (`/chat/session`, `/chat/messages`, `/chat/updates`). No hay un segundo camino: verificación por código, aprobaciones, mensajes del equipo, límites y auditoría son los mismos.

### 3. La página `/c/…` y la de la demo son el widget

- `/`, `/chat` y `/c/<enlace>` son ahora un marco mínimo que carga `widget.js` en modo página.
- Un solo código y un solo diseño. La página vieja (`index.html`) se borró.
- La vista previa del panel también es el widget real, en modo `inline`.

### 4. Dominios permitidos por empresa

- Cada empresa carga en el panel los sitios donde se puede usar su chat: `www.tutienda.com`, `*.tutienda.com` o `localhost`.
- El navegador manda el encabezado `Origin` en cada pedido. Si ese sitio no está en la lista, el servidor responde 403 y no crea la sesión ni corre el agente.
- **Sin dominios cargados, el widget no funciona en ningún sitio ajeno.** La página propia (`/c/…`) funciona siempre.
- La configuración pública (`/chat/widget/config`) responde `allowed: false` a un sitio no habilitado. El widget no se muestra y deja un aviso en la consola del navegador, para quien lo está instalando.
- El pedido previo del navegador (preflight, `OPTIONS`) se acepta siempre: no trae la sesión, así que no se sabe de qué empresa es. El control está en el pedido real.

**Límite conocido:** es un control de navegador, como CORS. Evita que otro sitio muestre el chat de la empresa a sus visitantes. Un programa que no es un navegador puede inventar el `Origin`; contra eso están los límites de uso por sesión, por IP y por día, que no cambian.

### 5. Marca por empresa

Tabla nueva `widget_settings` (migración 0017), con RLS como toda tabla de negocio.

- **Nombre** que ve el cliente, **color de acento** y **logo**.
- **Logo:** PNG, JPG o WebP de hasta 200 KB, guardado en la base. Se reconoce por sus primeros bytes, no por la extensión. SVG no se acepta: puede llevar scripts.
- **Preguntas sugeridas:** hasta 4. Se muestran con la conversación vacía.
- El panel tiene vista previa: cada cambio se ve antes de guardar.

### 6. Diseño

- **Aislado con Shadow DOM.** El CSS del sitio no entra al widget y el del widget no sale. La página de ejemplo tiene reglas agresivas a propósito para probarlo.
- **Sin `<form>` adentro.** Si el sitio envuelve la página en un formulario, el navegador descarta uno anidado y el chat quedaba sin campo de texto. Apareció al probar la vista previa del panel.
- **Tema claro y oscuro automáticos.** Primero mira el fondo del sitio; si no se puede saber, usa el del sistema.
- **Un solo color de acento.** El widget lo aclara u oscurece si no se distingue del fondo, y elige texto blanco o negro según el contraste.
- **Panel translúcido con desenfoque** en escritorio. En celular ocupa toda la pantalla y es opaco: con la página de fondo se veía sucio.
- **Movimiento:** apertura con rebote corto, mensajes que entran suaves, indicador de "escribiendo". Con `prefers-reduced-motion` no hay animaciones.
- **Celular:** pantalla completa, sigue al teclado (`visualViewport`) y respeta las áreas seguras.
- **Estados:** cargando, sin conexión, error con "Reintentar", límite alcanzado con "Empezar una conversación nueva", y conversación vacía con sugerencias.
- **Accesibilidad:** todo se usa con teclado, foco visible, `Esc` cierra, roles y nombres para lectores de pantalla, y el foco no se escapa del chat a pantalla completa.
- **Tipografía del sistema.** No se carga ninguna fuente.

### 7. Textos de los límites

En el chat de una empresa los mensajes de límite ya no dicen "la demo". Los de la demo de Acme no cambian.

## Qué se probó

- `test_widget.py`: archivo único y liviano, dominios permitidos (reglas y rutas), marca, validaciones, aislamiento entre empresas y el flujo de verificación desde otro origen.
- A mano en Chrome, con la página de ejemplo: escritorio y celular, claro y oscuro. Capturas en `docs/img/widget/`.

## Pendiente

- Probar en Safari de iPhone real: el teclado y las áreas seguras se revisaron en Chrome, con un marco de 390 px.
- Minificar `widget.js` y servirlo con caché larga y versión.
- Avisar con un sonido o una notificación cuando responde el equipo y la pestaña está en segundo plano.
- Idiomas: los textos del widget están en español.
