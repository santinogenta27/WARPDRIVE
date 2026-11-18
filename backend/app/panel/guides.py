"""Guías cortas de instalación, al lado del código para copiar (decisión 053). El mismo
contenido está en docs/instalacion.md para mandarlo por email."""

GUIDES = (
    (
        "Shopify",
        "<ol><li>En el admin: <b>Tienda online → Temas</b>.</li>"
        "<li>En el tema publicado: <b>⋯ → Editar código</b>.</li>"
        '<li>Abrí <span class="mono">layout/theme.liquid</span> y pegá el código justo antes de '
        '<span class="mono">&lt;/body&gt;</span>. Guardá.</li>'
        "<li>Si cambiás de tema, repetí el paso 3.</li></ol>",
    ),
    (
        "WordPress",
        "<ol><li><b>Plugins → Añadir nuevo</b>: instalá y activá <b>WPCode</b>.</li>"
        "<li><b>Code Snippets → Header &amp; Footer</b>.</li>"
        "<li>Pegá el código en <b>Footer</b> y guardá.</li></ol>",
    ),
    (
        "Tiendanube",
        "<ol><li>En el administrador: <b>Configuración → Códigos externos</b>.</li>"
        "<li>Pegá el código en el campo de códigos de seguimiento y guardá.</li>"
        "<li>Si tu plan no lo tiene, usá el enlace del chat de prueba como botón en el menú.</li>"
        "</ol>",
    ),
    (
        "WhatsApp",
        "<p>Si tu empresa tiene un número de WhatsApp Business verificado por Meta, el mismo "
        "asistente atiende ahí. Conectalo vos mismo en <b>Canales → WhatsApp</b>, más abajo, "
        "con la guía paso a paso.</p>",
    ),
)


GUIDES_EN = (
    (
        "Shopify",
        "<ol><li>In the admin: <b>Online Store → Themes</b>.</li>"
        "<li>On the live theme: <b>⋯ → Edit code</b>.</li>"
        '<li>Open <span class="mono">layout/theme.liquid</span> and paste the code right before '
        '<span class="mono">&lt;/body&gt;</span>. Save.</li>'
        "<li>If you switch themes, repeat step 3.</li></ol>",
    ),
    (
        "WordPress",
        "<ol><li><b>Plugins → Add New</b>: install and activate <b>WPCode</b>.</li>"
        "<li><b>Code Snippets → Header &amp; Footer</b>.</li>"
        "<li>Paste the code in <b>Footer</b> and save.</li></ol>",
    ),
    (
        "Tiendanube",
        "<ol><li>In the admin: <b>Settings → External codes</b>.</li>"
        "<li>Paste the code in the tracking codes field and save.</li>"
        "<li>If your plan doesn't have it, use the test chat link as a button in the menu.</li>"
        "</ol>",
    ),
    (
        "WhatsApp",
        "<p>If your company has a WhatsApp Business number verified by Meta, the same assistant "
        "can answer there. Connect it yourself under <b>Channels → WhatsApp</b>, below, with "
        "the step-by-step guide.</p>",
    ),
)


# Conectar el número de WhatsApp de la empresa con su propia app de Meta (decisión 068).
WHATSAPP = (
    "<ol>"
    '<li>En <span class="mono">developers.facebook.com</span>: <b>Mis apps → Crear app</b>, '
    "tipo <b>Empresa</b>, y vinculala al portfolio comercial (Business Manager) de tu "
    "empresa.</li>"
    "<li>En la app: <b>Agregar producto → WhatsApp</b>. En <b>WhatsApp → Configuración de la "
    "API</b> agregá tu número real (lo verifica Meta por SMS o llamada) y registralo. Ahí ves "
    "el <b>Phone Number ID</b> y el <b>WhatsApp Business Account ID</b>.</li>"
    '<li>En <span class="mono">business.facebook.com</span>: <b>Configuración del negocio → '
    "Usuarios → Usuarios del sistema → Agregar</b>, con rol <b>Administrador</b>. Asignale "
    "la app y la cuenta de WhatsApp, las dos con control total.</li>"
    "<li>Con ese usuario del sistema: <b>Generar token</b>, elegí la app, vencimiento "
    "<b>Nunca</b> y marcá los permisos "
    '<span class="mono">whatsapp_business_messaging</span> y '
    '<span class="mono">whatsapp_business_management</span>. Copiá el token: Meta no lo vuelve '
    "a mostrar.</li>"
    "<li>En la app: <b>Configuración de la app → Básica → App Secret → Mostrar</b> y "
    "copialo.</li>"
    "<li>Acá, en <b>Canales → WhatsApp</b>: pegá el Phone Number ID, el WhatsApp Business "
    "Account ID, el token y el App Secret, y guardá.</li>"
    "<li>En la app: <b>WhatsApp → Configuración → Webhook → Editar</b>. Pegá la URL del webhook "
    "y el verify token que te mostramos acá, tocá <b>Verificar y guardar</b> y suscribite al "
    "campo <b>messages</b>.</li>"
    "<li>Acá: <b>Probar conexión</b>, <b>Enviar mensaje de prueba</b> y <b>Activar</b>. Para "
    "que escriba cualquier cliente, la app de Meta tiene que estar publicada (modo "
    "<b>Live</b>).</li>"
    "</ol>"
)

WHATSAPP_EN = (
    "<ol>"
    '<li>At <span class="mono">developers.facebook.com</span>: <b>My apps → Create app</b>, '
    "type <b>Business</b>, and link it to your company's business portfolio (Business "
    "Manager).</li>"
    "<li>In the app: <b>Add product → WhatsApp</b>. Under <b>WhatsApp → API Setup</b>, add your "
    "real number (Meta verifies it by SMS or call) and register it. There you'll see the "
    "<b>Phone Number ID</b> and the <b>WhatsApp Business Account ID</b>.</li>"
    '<li>At <span class="mono">business.facebook.com</span>: <b>Business settings → Users → '
    "System users → Add</b>, with the <b>Admin</b> role. Assign it the app and the WhatsApp "
    "account, both with full control.</li>"
    "<li>With that system user: <b>Generate token</b>, pick the app, expiration <b>Never</b> "
    'and check the <span class="mono">whatsapp_business_messaging</span> and '
    '<span class="mono">whatsapp_business_management</span> permissions. Copy the token: Meta '
    "won't show it again.</li>"
    "<li>In the app: <b>App settings → Basic → App Secret → Show</b> and copy it.</li>"
    "<li>Here, under <b>Channels → WhatsApp</b>: paste the Phone Number ID, the WhatsApp "
    "Business Account ID, the token and the App Secret, and save.</li>"
    "<li>In the app: <b>WhatsApp → Configuration → Webhook → Edit</b>. Paste the webhook URL "
    "and the verify token we show you here, click <b>Verify and save</b> and subscribe to the "
    "<b>messages</b> field.</li>"
    "<li>Here: <b>Test connection</b>, <b>Send test message</b> and <b>Activate</b>. For any "
    "customer to write, the Meta app has to be published (<b>Live</b> mode).</li>"
    "</ol>"
)


def whatsapp_guide(*, english: bool | None = None) -> str:
    from app.panel import english as language

    return WHATSAPP_EN if (language.english() if english is None else english) else WHATSAPP


def guides_html() -> str:
    from app.panel import english

    en = english.english()
    items = "".join(
        f'<details style="margin:6px 0"><summary>{"Install it on" if en else "Instalarlo en"} '
        f'{name}</summary><div class="small">{body}</div></details>'
        for name, body in (GUIDES_EN if en else GUIDES)
    )
    note = (
        "First add your store's domain under Allowed domains (step 2)."
        if en
        else "Primero cargá el dominio de tu tienda en Dominios permitidos (paso 2)."
    )
    return f'<p class="muted small">{note}</p>{items}'
