# Instalar el asistente en tu tienda

Guías cortas para las plataformas más usadas. En todas, el código es el mismo: copialo del panel en **Configuración → Widget para tu sitio → Copiar código**. Se ve así:

```html
<script src="https://TU-SERVICIO/widget.js" data-chat="ENLACE-DE-TU-CHAT" async></script>
```

**Antes de pegarlo**, cargá el dominio de tu tienda en **Dominios permitidos**, en la misma pantalla (por ejemplo `www.tutienda.com`). Si no, el chat no abre en tu sitio. Detalle y opciones en [widget.md](widget.md).

Los menús de cada plataforma cambian seguido. Si algún nombre no coincide, buscá la opción para agregar código en el pie de todas las páginas.

## Shopify

1. En el admin: **Tienda online → Temas**.
2. En el tema publicado: **⋯ → Editar código**.
3. Abrí `layout/theme.liquid`.
4. Pegá el código justo antes de `</body>` y tocá **Guardar**.
5. Abrí la tienda: la burbuja aparece abajo a la derecha.

Al cambiar de tema, repetí el paso 4 en el tema nuevo.

## WordPress (y WooCommerce)

Con un plugin, sin tocar el tema:

1. **Plugins → Añadir nuevo** → buscá **WPCode** (antes "Insert Headers and Footers") → **Instalar** y **Activar**.
2. **Code Snippets → Header & Footer**.
3. Pegá el código en **Footer** y tocá **Guardar cambios**.

Sin plugin: **Apariencia → Editor de archivos del tema → `footer.php`**, antes de `</body>`. Una actualización del tema lo puede borrar; por eso conviene el plugin.

## Tiendanube

1. En el administrador: **Configuración → Códigos externos**.
2. Pegá el código en el campo de códigos de seguimiento que se carga en todas las páginas.
3. Guardá y abrí la tienda.

Si tu plan no tiene esa opción, el chat igual funciona con su enlace propio (`/c/…`, en el panel): ponelo como botón o en el menú de la tienda.

## WhatsApp

Si tu empresa tiene un número de WhatsApp Business verificado por Meta, el mismo asistente puede atender ahí. Hoy lo conecta el operador: escribinos. Detalle técnico en [whatsapp.md](whatsapp.md).

## Probar que quedó bien

- Abrí la tienda en una ventana privada y escribí "hola".
- Mirá la conversación en el panel, en **Casos**.
- Si la burbuja no aparece, abrí la consola del navegador: si dice que el dominio no está en la lista, falta cargarlo en **Dominios permitidos**.
