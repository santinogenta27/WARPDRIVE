# Instalar el agente en tu sitio

Las mismas guías que muestra el panel en **Deploy → Add the chat to your website**, para mandarlas por email. El código es el mismo en todas las plataformas: copialo con **Copy code**. Se ve así:

```html
<script src="https://TU-SERVICIO/widget.js" data-chat="ENLACE-DE-TU-CHAT" async></script>
```

**Dominios permitidos.** El chat solo abre en los dominios cargados en **Allowed domains** (mismo paso). Si conectaste tu tienda de Shopify o Tiendanube en el paso 1, sus dominios ya están cargados. Detalle en [widget.md](widget.md).

**Revisar la instalación.** Después de pegar el código y publicar, tocá **Check installation**: confirma que el chat cargó desde tu dominio, o te dice si el código no está en la página.

Menús verificados en la documentación oficial el 23/11/2026 (decisión 086).

## Shopify

1. En el admin: **Online Store** (Tienda online) → **Themes** (Temas).
2. Al lado del tema actual: **⋯** → **Edit code** (Editar código).
3. En la carpeta **Layout**, abrí `theme.liquid`.
4. Pegá el código justo antes de `</body>` y tocá **Save**.
5. Abrí la tienda: la burbuja aparece abajo a la derecha.

Si cambiás de tema, pegá el código de nuevo. Shopify recomienda que las apps usen un "app embed" (Theme settings → App embeds): llega cuando la app de la plataforma tenga su extensión de tema (decisión 086).

## WordPress (y WooCommerce)

**Sin plugin** (como administrador):

1. Temas de bloques: **Apariencia → Editor → Patrones → Pie de página**. Temas clásicos: **Apariencia → Widgets**, un área del pie.
2. Agregá un bloque **HTML personalizado**, pegá el código y guardá.

**Con un plugin**, si el tema no tiene un pie editable:

1. **Plugins → Añadir nuevo plugin**, buscá **WPCode**, **Instalar ahora** y **Activar**.
2. **Code Snippets → Header & Footer**, pegá el código en **Footer** y tocá **Save Changes**.

## Tiendanube

Desde marzo de 2024 Tiendanube no tiene el campo de "códigos de tracking". La forma oficial de sumar código externo es Google Tag Manager (solo planes pagos):

1. En Google Tag Manager, creá una etiqueta **HTML personalizado** con el código, activador **Todas las páginas**, y publicá.
2. En el administrador de Tiendanube: **Configuración → Códigos externos**, pegá el ID del contenedor (`GTM-XXXXXXX`) en **Google GTM ID** y **Guardar cambios**.

En cualquier plan: **Diseño → Editar el código de diseño** y pegá el código antes de `</body>` en el layout del tema, o agregá al menú un link a la página de tu chat.

## Cualquier otro sitio

Pegá el código antes de `</body>` en todas las páginas (en el pie o el layout compartido). En los creadores de sitios:

- **Wix:** Configuración → Código personalizado.
- **Squarespace:** Configuración → Avanzado → Inyección de código → Pie de página.
- **Webflow:** Site settings → Custom code → Footer code.

## WhatsApp

El mismo agente atiende en tu número de WhatsApp Business. Se conecta desde **Deploy → Connect WhatsApp**, con la guía paso a paso de [whatsapp.md](whatsapp.md) y la decisión 068.
