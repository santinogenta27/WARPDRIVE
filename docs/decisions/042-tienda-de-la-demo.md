# 042 — La tienda de la demo en inglés

**Estado:** adoptada (16 de noviembre de 2026). Rediseño del sitio de `/demo/northwind-outfitters` para el video de Upwork. Decidida sin aprobación previa: revisala.

## Contexto

En la vista "detrás de escena" (decisión 041), el sitio de la izquierda era un header, una portada con degradé y cinco cuadrados de color. Para el video tiene que parecer el sitio de una marca outdoor premium real, sin copiar ninguna y sin tocar el widget ni el panel.

## Decisiones

### 1. Estructura de tienda real, armada en `app/demo/store.py`

Aviso de envío y devoluciones, header fijo (logo, navegación, buscador, cuenta y carrito), portada con un paisaje ilustrado y "Shop the collection", los ocho más vendidos, una franja de confianza y un footer con columnas y el aviso "Demo · Northwind Outfitters is a fictional store".

- **Todo es decorativo:** no hay formularios (el buscador es un `span`) ni enlaces que lleven a otro lado. Los puntos de color de cada producto sí responden: pintan la ilustración con ese color.
- **Otra empresa** (por ejemplo Acme, en español) recibe solo header, portada y pie, sin nada propio de Northwind: ni envíos, ni garantía, ni catálogo.

### 2. Ilustraciones SVG propias, sin fotos

Una por producto en `app/demo/static/store/` (campera, botas, sweater, chaleco, pantalón, remera, bolso y gorro) y el paisaje de la portada. Planas, en capas, sin logos ni marcas. Cada una pinta sus luces y sombras desde un solo color (`--p`) con `color-mix`, así el mismo dibujo sirve para todos los colores del catálogo.

- Los productos de los pedidos del guion van primero y en el color del pedido: campera Lake Blue (NW-4521), botas Walnut y gorro Heather Gray (NW-4528), sweater Navy (NW-4533) y pantalón Olive (NW-4540).
- Nada se carga de internet: ni fotos, ni fuentes, ni scripts.

### 3. Tipografía y paleta

- **Tipografía del sistema**, la misma del widget, con jerarquía por tamaño y peso. Una fuente web sería una dependencia externa para la demo y otra familia distinta de la del chat.
- **Paleta sobria:** verde bosque (el color de Northwind), arena y carbón, más los tokens del widget (`--ink`, `--muted`, `--surface`, `--line`, `--rim`). El test los compara con los de `widget.js`.

### 4. El chat no compite

- El chat sigue abajo a la derecha y el panel a la derecha. El sitio se desplaza por dentro (`.store`) y por detrás del chat: el chat queda fuera de lo que se desplaza.
- Desde 900 px de ancho del sitio, el contenido deja libre la columna del chat (`--chat-lane`) y los fondos siguen por detrás. A 1456 px los productos van en dos columnas; a 1920, en cuatro.
- El footer es claro en el tema claro: oscuro, el vidrio del chat se veía gris encima.

### 5. Tema claro y oscuro, forzable

El tema sigue al sistema, como antes, y además se puede forzar con `?theme=light` o `?theme=dark`. Sirve para grabar el video en el tema que se quiera sin cambiar Windows. Lo aplica un script del `<head>` en `data-theme`; las variantes oscuras del panel pasaron de la media query a ese atributo, sin cambiar cómo se ve.

### 6. Movimiento sutil

Al pasar el mouse por un producto: la ilustración sube unos píxeles y crece un 4 %, el fondo de la tarjeta se oscurece y el nombre se subraya. Con movimiento reducido (como en la máquina de desarrollo) no hay desplazamiento: quedan el cambio de fondo y el subrayado.
