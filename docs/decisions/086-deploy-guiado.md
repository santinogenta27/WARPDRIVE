# 086 — Deploy: una pantalla guiada para poner el agente en marcha

**Contexto.** Para salir en vivo, una empresa tenía que encontrar cosas en tres lugares de una página de Configuración: el sistema de la empresa, el widget y WhatsApp, y salir en vivo estaba en el Reporte. Las guías de WordPress, Tiendanube y WhatsApp eran de una línea, y la prueba de conexión daba un mensaje técnico.

## Qué se decidió

**Una pantalla, cinco pasos con su estado** (`/panel/deploy`, `app/companies/deploy.py`):

| Paso | Estado |
|---|---|
| 1. Connect your store | Connected · Test mode · Not connected · Demo store |
| 2. Choose what your agent can do | "N of M on" · None on yet · Connect your store first |
| 3. Add the chat to your website | Installed · Not installed |
| 4. Connect WhatsApp (opcional) | Connected · Not active · Optional |
| 5. Go live | Live · Paused · Ready · Not ready |

**Paso 1, la tienda.** Tres opciones: Shopify, Tiendanube u otra plataforma con su OpenAPI.
- Shopify: los tres métodos de la decisión 067. "Install with Shopify" (OAuth) aparece solo si están `SHOPIFY_CLIENT_ID` y `SHOPIFY_CLIENT_SECRET`, y va primero porque es lo más rápido.
- **Conectar prueba la conexión en el momento.** Antes eran dos clics y el segundo se olvidaba.
- **Qué falta y qué hacer.** El resultado se explica en una de cuatro categorías, a partir del código del conector:

| Falta | Códigos | Qué hacer (resumen) |
|---|---|---|
| Un permiso | `missing_scope` (con la lista) | Versions → Scopes → Release, y conectar de nuevo |
| Una credencial | `token_invalid`, `forbidden`, `invalid_client`, 401/403 | Copiar de nuevo el Client ID y el secret desde Settings |
| La tienda | `shop_not_permitted`, `store_not_found`, `shop_frozen`, tienda que no responde | Revisar la dirección; misma organización; o Install with Shopify |
| Tu sistema | otro error | Probar de nuevo, revisar la URL base |

  `shopify_auth` ahora lanza `TokenError` con el código. Tiendanube tiene su propia prueba (`GET /store`): token rechazado o número de tienda inexistente.

**Paso 3, el sitio.**
- **Los dominios de la tienda se agregan solos** a los permitidos cuando la prueba pasa: el `myshopifyDomain` y el `primaryDomain` de Shopify, y la URL, el dominio original y los dominios de Tiendanube. No se borran los que ya estaban. Si la empresa todavía no había guardado su chat, la fila de `widget_settings` nace "automática" (migración 0036) y no cuenta como "marca configurada" en los primeros pasos.
- **Check installation.**
  - Si el chat ya pidió su configuración desde un dominio permitido (lo registra `first_steps.note_widget_seen`, ahora con el dominio), está instalado.
  - Si no, se lee la página de inicio de hasta tres dominios permitidos (solo direcciones públicas, decisión 076) y se busca `data-chat="<enlace>"`.
  - Cuatro respuestas: instalado, código encontrado pero sin cargar todavía, código no encontrado, o el sitio no abre.
- **Guías completas** (`app/panel/guides.py` y `docs/instalacion.md`), en inglés y en español. Los nombres de menús de otros productos se muestran tal cual (no se traducen).

## Shopify: theme.liquid ahora, app embed después

- **Opciones:** (a) pegar el código en `theme.liquid`; (b) una extensión de tema con un "app embed" (Theme settings → App embeds).
- **Lo que dice Shopify:** recomienda los app embeds para burbujas de chat flotantes. Editar el código del tema puede dejarlo sin actualizaciones automáticas.
- **Por qué no (b) ahora:** un app embed necesita una app de la plataforma con su extensión publicada con Shopify CLI y distribuida a cada tienda. Es un proyecto aparte: no hay app pública todavía.
- **Elegida: (a)** con la advertencia de repetirlo al cambiar de tema. Queda como siguiente paso: cuando exista la app de OAuth de la plataforma, sumarle la extensión con el app embed y un link directo para activarlo.

## Tiendanube: Google Tag Manager

- Desde el 03/03/2024 Tiendanube sacó los campos de "códigos de tracking". "Otros códigos de conversión" solo corre en las páginas de fin de compra: no sirve para un chat.
- La vía oficial para código externo es Google Tag Manager: Configuración → Códigos externos → "Google GTM ID". Solo en planes pagos.
- En cualquier plan: Diseño → Editar el código de diseño, o un link al chat en el menú.

## Verificación de punta a punta

`evals/shopify_e2e.py` recorre lo mismo que una empresa contra una tienda de desarrollo real:
- token con Client ID y secret;
- permisos;
- conectar, probar y sumar dominios;
- consultar un pedido y productos;
- con `--write`, crear un cupón.

La base es `DATABASE_URL_STAGING`, en una transacción revertida. Sin las variables `SHOPIFY_TEST_*`, el script dice qué crear en Shopify: la tienda de desarrollo tiene que crearse desde **Dev stores** del Dev Dashboard, en la misma organización que la app.

## Fuentes (leídas el 23/11/2026)

- Shopify:
  - Dev Dashboard: crear app, Versions → Scopes → Release, Install app, Settings → Client ID / Client secret.
  - `client_credentials`: 24 h, misma organización, `shop_not_permitted`.
  - Apps propias del admin: legacy, solo las creadas antes del 01/01/2026.
  - App embeds.
  - `shopify.dev/docs/apps/build/dev-dashboard/create-apps-using-dev-dashboard`, `.../get-api-access-tokens`, `help.shopify.com/en/manual/apps/app-types/custom-apps`, `shopify.dev/docs/apps/build/online-store/theme-app-extensions/configuration`.
- Tiendanube: `ayuda.tiendanube.com` ("Códigos externos", "Cómo vincular herramientas externas… luego de la desactivación de los códigos de tracking", "Cómo instalar Google Tag Manager").
- Meta: `developers.facebook.com/docs/whatsapp/cloud-api/get-started`:
  - "Create App" y el caso de uso "Connect with customers through WhatsApp";
  - API Setup;
  - Business settings → System users → Assign assets → Generate token;
  - Configuration → messages;
  - modo Live.

## Sin verificar

- Meta: los nombres exactos "Callback URL", "Verify token" y "Verify and save", la ubicación del App Secret (App settings → Basic) y el vencimiento "Never" del token. La guía los nombra de forma genérica donde no se pudo confirmar.
- WordPress y WPCode: los nombres salen de la interfaz conocida, no de una página leída hoy.
- Wix, Squarespace y Webflow: idem.
- Tiendanube: el nombre del archivo de layout del tema.
- Todo el flujo de Shopify contra una tienda real: queda listo `evals/shopify_e2e.py`.
