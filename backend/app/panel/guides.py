"""Guías paso a paso de Deploy (decisiones 053, 067, 068 y 086), en inglés y en español.

Los nombres de menús salen de la documentación oficial, leída el 23/11/2026 (las fuentes y lo
que no se pudo verificar están en la decisión 086). Cada guía es HTML corto: una lista de pasos
con los nombres de los botones en negrita. El mismo contenido está en docs/instalacion.md.
"""

from app.panel import english as panel_language


def _scopes() -> str:
    from app.connectors.shopify_auth import SCOPES

    return ", ".join(f'<span class="mono">{s}</span>' for s in SCOPES)


def _pick(en: str, es: str) -> str:
    # Cada guía ya está en el idioma del panel y nombra menús de otros productos tal cual
    # aparecen ("Códigos externos", "Save Changes"): no se traduce.
    return f'<div translate="no">{en if panel_language.english() else es}</div>'


# -- 1. conectar Shopify (decisión 067) -----------------------------------------------------------
SHOPIFY_APP_EN = """<ol class="guide">
<li>Open the <a href="https://dev.shopify.com/dashboard/" target="_blank" rel="noopener">Shopify
Dev Dashboard</a> with the account that owns your store, and go to <b>Apps</b>.</li>
<li>Click <b>Create app</b> → <b>Start from Dev Dashboard</b>, name it (for example
<i>Support agent</i>) and click <b>Create</b>.</li>
<li>In <b>Versions</b>, fill in <b>Scopes</b> with exactly these permissions: {scopes}. For
<b>App URL</b> you can use your store's address. Click <b>Release</b>.</li>
<li>Click <b>Install app</b> (at the top of the <b>Installs</b> card), choose your store and click
<b>Install</b>.</li>
<li>Open the app's <b>Settings</b> and copy the <b>Client ID</b> and the <b>Client secret</b>.</li>
<li>Paste them here with your store address (<span class="mono">yourstore.myshopify.com</span>)
and click <b>Connect</b>. We test the connection right away.</li>
</ol>
<p class="help">The app and the store must belong to the same Shopify organization. Shopify's
access lasts 24 hours and we renew it automatically. To read customers' names, emails and phone
numbers, Shopify asks the app to request access to <b translate="no">Protected customer data</b>.
</p>"""

SHOPIFY_APP_ES = """<ol class="guide">
<li>Abrí el <a href="https://dev.shopify.com/dashboard/" target="_blank" rel="noopener">Dev
Dashboard de Shopify</a> con la cuenta dueña de tu tienda y entrá a <b>Apps</b>.</li>
<li>Tocá <b>Create app</b> → <b>Start from Dev Dashboard</b>, ponele un nombre (por ejemplo
<i>Agente de atención</i>) y tocá <b>Create</b>.</li>
<li>En <b>Versions</b>, completá <b>Scopes</b> con exactamente estos permisos: {scopes}. En
<b>App URL</b> podés poner la dirección de tu tienda. Tocá <b>Release</b>.</li>
<li>Tocá <b>Install app</b> (arriba de la tarjeta <b>Installs</b>), elegí tu tienda y tocá
<b>Install</b>.</li>
<li>Abrí <b>Settings</b> de la app y copiá el <b>Client ID</b> y el <b>Client secret</b>.</li>
<li>Pegalos acá con la dirección de tu tienda (<span class="mono">tutienda.myshopify.com</span>)
y tocá <b>Conectar</b>. Probamos la conexión en el momento.</li>
</ol>
<p class="help">La app y la tienda tienen que ser de la misma organización de Shopify. El acceso
que da Shopify dura 24 horas y lo renovamos solos. Para leer el nombre, el email y el teléfono de
los clientes, Shopify pide que la app solicite acceso a
<b translate="no">Protected customer data</b>.</p>"""


def shopify_app_guide() -> str:
    return _pick(SHOPIFY_APP_EN, SHOPIFY_APP_ES).format(scopes=_scopes())


TIENDANUBE_EN = """<ol class="guide">
<li>Your Tiendanube store number is in your admin's address:
<span class="mono">yourstore.mitiendanube.com/admin/v2/<b>123456</b></span>.</li>
<li>Create an app in the <a href="https://partners.tiendanube.com" target="_blank"
rel="noopener">Tiendanube Partners portal</a>, install it on your store and copy its access
token.</li>
<li>Paste the store number and the token here and click <b>Connect</b>.</li></ol>"""

TIENDANUBE_ES = """<ol class="guide">
<li>El número de tu tienda está en la dirección de tu administrador:
<span class="mono">tutienda.mitiendanube.com/admin/v2/<b>123456</b></span>.</li>
<li>Creá una app en el <a href="https://partners.tiendanube.com" target="_blank"
rel="noopener">portal de Partners de Tiendanube</a>, instalala en tu tienda y copiá su token de
acceso.</li>
<li>Pegá el número de la tienda y el token acá y tocá <b>Conectar</b>.</li></ol>"""


def tiendanube_guide() -> str:
    return _pick(TIENDANUBE_EN, TIENDANUBE_ES)


# -- 3. instalar el chat en el sitio (decisiones 053 y 086) ----------------------------------------
INSTALL_EN = {
    "shopify": """<ol class="guide">
<li>In your Shopify admin, go to <b>Online Store</b> → <b>Themes</b>.</li>
<li>Next to your current theme, click <b>⋯</b> → <b>Edit code</b>.</li>
<li>In the <b>Layout</b> folder, open <span class="mono">theme.liquid</span>.</li>
<li>Paste the code right before <span class="mono">&lt;/body&gt;</span> and click <b>Save</b>.
</li>
<li>Open your store: the chat bubble shows up at the bottom right. Then click
<b>Check installation</b> here.</li></ol>
<p class="help">If you switch to another theme, paste the code again in the new one.</p>""",
    "wordpress": """<p><b>Without a plugin</b> (you need to be an administrator):</p>
<ol class="guide">
<li>Block themes: go to <b>Appearance</b> → <b>Editor</b> → <b>Patterns</b> →
<b>Footer</b>. Classic themes: go to <b>Appearance</b> → <b>Widgets</b> and open a footer area.
</li>
<li>Add a <b>Custom HTML</b> block, paste the code and click <b>Save</b>.</li></ol>
<p><b>With a plugin</b>, if your theme doesn't have a footer you can edit:</p>
<ol class="guide">
<li>Go to <b>Plugins</b> → <b>Add New Plugin</b>, search for <b>WPCode</b>, click
<b>Install Now</b> and then <b>Activate</b>.</li>
<li>Go to <b>Code Snippets</b> → <b>Header &amp; Footer</b>, paste the code in the <b>Footer</b>
box and click <b>Save Changes</b>.</li></ol>""",
    "tiendanube": """<p><b>With Google Tag Manager</b> (Tiendanube's official way to add external
code; paid plans only):</p>
<ol class="guide">
<li>In <a href="https://tagmanager.google.com" target="_blank" rel="noopener">Google Tag
Manager</a>, create a <b>Custom HTML</b> tag with the code, with the <b>All Pages</b> trigger,
and publish it.</li>
<li>In your Tiendanube admin, go to <b>Configuración</b> → <b>Códigos externos</b>, paste your
container ID (<span class="mono">GTM-XXXXXXX</span>) in <b>Google GTM ID</b> and click
<b>Guardar cambios</b>.</li></ol>
<p><b>On any plan:</b> go to <b>Diseño</b> → <b>Editar el código de diseño</b> and paste the code
right before <span class="mono">&lt;/body&gt;</span> in your theme's layout. Or add a link to
your chat page to your store's menu.</p>""",
    "other": """<ol class="guide">
<li>Paste the code right before <span class="mono">&lt;/body&gt;</span> on every page where you
want the chat (usually in a shared footer or layout).</li>
<li>Site builders have a place for it: <b>Wix</b>: Settings → Custom Code. <b>Squarespace</b>:
Settings → Advanced → Code Injection → Footer. <b>Webflow</b>: Site settings → Custom code →
Footer code.</li>
<li>Publish your site and click <b>Check installation</b> here.</li></ol>""",
}

INSTALL_ES = {
    "shopify": """<ol class="guide">
<li>En el admin de Shopify, entrá a <b>Online Store</b> (Tienda online) → <b>Themes</b>
(Temas).</li>
<li>Al lado de tu tema actual, tocá <b>⋯</b> → <b>Edit code</b> (Editar código).</li>
<li>En la carpeta <b>Layout</b>, abrí <span class="mono">theme.liquid</span>.</li>
<li>Pegá el código justo antes de <span class="mono">&lt;/body&gt;</span> y tocá <b>Save</b>
(Guardar).</li>
<li>Abrí tu tienda: la burbuja del chat aparece abajo a la derecha. Después tocá
<b>Revisar la instalación</b> acá.</li></ol>
<p class="help">Si cambiás de tema, pegá el código de nuevo en el tema nuevo.</p>""",
    "wordpress": """<p><b>Sin plugin</b> (tenés que ser administrador):</p>
<ol class="guide">
<li>Temas de bloques: entrá a <b>Apariencia</b> → <b>Editor</b> → <b>Patrones</b> →
<b>Pie de página</b>. Temas clásicos: entrá a <b>Apariencia</b> → <b>Widgets</b> y abrí un área
del pie de página.</li>
<li>Agregá un bloque <b>HTML personalizado</b>, pegá el código y tocá <b>Guardar</b>.</li></ol>
<p><b>Con un plugin</b>, si tu tema no tiene un pie de página editable:</p>
<ol class="guide">
<li>Entrá a <b>Plugins</b> → <b>Añadir nuevo plugin</b>, buscá <b>WPCode</b>, tocá
<b>Instalar ahora</b> y después <b>Activar</b>.</li>
<li>Entrá a <b>Code Snippets</b> → <b>Header &amp; Footer</b>, pegá el código en el recuadro
<b>Footer</b> y tocá <b>Save Changes</b>.</li></ol>""",
    "tiendanube": """<p><b>Con Google Tag Manager</b> (la forma oficial de Tiendanube para sumar
códigos externos; solo en planes pagos):</p>
<ol class="guide">
<li>En <a href="https://tagmanager.google.com" target="_blank" rel="noopener">Google Tag
Manager</a>, creá una etiqueta <b>HTML personalizado</b> con el código, con el activador
<b>Todas las páginas</b>, y publicala.</li>
<li>En el administrador de Tiendanube, entrá a <b>Configuración</b> → <b>Códigos externos</b>,
pegá el ID de tu contenedor (<span class="mono">GTM-XXXXXXX</span>) en <b>Google GTM ID</b> y
tocá <b>Guardar cambios</b>.</li></ol>
<p><b>En cualquier plan:</b> entrá a <b>Diseño</b> → <b>Editar el código de diseño</b> y pegá el
código justo antes de <span class="mono">&lt;/body&gt;</span> en el layout de tu tema. O agregá
al menú de tu tienda un link a la página de tu chat.</p>""",
    "other": """<ol class="guide">
<li>Pegá el código justo antes de <span class="mono">&lt;/body&gt;</span> en cada página donde
quieras el chat (normalmente, en el pie o el layout compartido).</li>
<li>Los creadores de sitios tienen un lugar para esto: <b>Wix</b>: Configuración → Código
personalizado. <b>Squarespace</b>: Configuración → Avanzado → Inyección de código → Pie de
página. <b>Webflow</b>: Site settings → Custom code → Footer code.</li>
<li>Publicá tu sitio y tocá <b>Revisar la instalación</b> acá.</li></ol>""",
}
INSTALL_NAMES = (
    ("shopify", "Shopify"),
    ("wordpress", "WordPress"),
    ("tiendanube", "Tiendanube"),
    ("other", "Any website"),
)


def install_guide(key: str) -> str:
    found = (INSTALL_EN if panel_language.english() else INSTALL_ES)[key]
    return f'<div translate="no">{found}</div>'


# -- 4. WhatsApp (decisión 068) -----------------------------------------------------------------
WHATSAPP_EN = """<p>You need a WhatsApp Business number (one that isn't used in the WhatsApp app
on a phone) and about 20 minutes. Everything happens in your company's Meta account.</p>
<ol class="guide">
<li><b>Create the app.</b> In <a href="https://developers.facebook.com/apps" target="_blank"
rel="noopener">Meta for Developers</a>, click <b>Create App</b>, choose the use case
<b>Connect with customers through WhatsApp</b>, pick your business portfolio and click
<b>Create app</b>.</li>
<li><b>Add your number.</b> In the app, open <b>API Setup</b>, choose or create your WhatsApp
Business account and add your number (Meta verifies it by text message or call). On that page
you'll see the <b>Phone Number ID</b> and the <b>WhatsApp Business Account ID</b>: paste them
here.</li>
<li><b>Create a permanent key.</b> In <a href="https://business.facebook.com/latest/settings"
target="_blank" rel="noopener">Business settings</a> → <b>System users</b>, click <b>Add</b> to
create a system user. Then <b>Assign assets</b>: your app and your WhatsApp account, both with
full control. Click <b>Generate token</b> with the permissions
<span class="mono">whatsapp_business_messaging</span>
and <span class="mono">whatsapp_business_management</span>. Copy it and paste it here as the
permanent token (Meta shows it only once).</li>
<li><b>Copy the App Secret</b> from the app's <b>App settings</b> → <b>Basic</b> and paste it
here. Click <b>Save</b>.</li>
<li><b>Connect the webhook.</b> In the app, open <b>Configuration</b> (under WhatsApp), paste the
callback URL and the verify token we show you here, verify them and subscribe to
<b>messages</b>.</li>
<li><b>Test and turn it on.</b> Here: <b>Test connection</b>, send a test message and click
<b>Activate</b>. For any customer to reach you, the Meta app must be in <b>Live</b> mode.</li>
</ol>"""

WHATSAPP_ES = """<p>Necesitás un número de WhatsApp Business (que no se use en la app de
WhatsApp de un celular) y unos 20 minutos. Todo pasa en la cuenta de Meta de tu empresa.</p>
<ol class="guide">
<li><b>Creá la app.</b> En <a href="https://developers.facebook.com/apps" target="_blank"
rel="noopener">Meta for Developers</a>, tocá <b>Create App</b>, elegí el caso de uso
<b>Connect with customers through WhatsApp</b>, tu portfolio comercial y tocá
<b>Create app</b>.</li>
<li><b>Agregá tu número.</b> En la app, abrí <b>API Setup</b>, elegí o creá tu cuenta de WhatsApp
Business y agregá tu número (Meta lo verifica por SMS o llamada). En esa página están el
<b>Phone Number ID</b> y el <b>WhatsApp Business Account ID</b>: pegalos acá.</li>
<li><b>Creá una clave permanente.</b> En la <a href="https://business.facebook.com/latest/settings"
target="_blank" rel="noopener">configuración del negocio</a> → <b>Usuarios del sistema</b>, tocá
<b>Agregar</b> para crear un usuario del sistema. Después <b>Asignar activos</b>: tu app y tu
cuenta de WhatsApp, las dos con
control total. Tocá <b>Generar token</b> con los permisos
<span class="mono">whatsapp_business_messaging</span> y
<span class="mono">whatsapp_business_management</span>. Copialo y pegalo acá como token
permanente (Meta lo muestra una sola vez).</li>
<li><b>Copiá el App Secret</b> de <b>Configuración de la app</b> → <b>Básica</b> y pegalo acá.
Tocá <b>Guardar</b>.</li>
<li><b>Conectá el webhook.</b> En la app, abrí <b>Configuration</b> (dentro de WhatsApp), pegá
la URL y el token de verificación que te mostramos acá, verificalos y suscribite a
<b>messages</b>.</li>
<li><b>Probá y activá.</b> Acá: <b>Probar conexión</b>, mandá un mensaje de prueba y tocá
<b>Activar</b>. Para que te escriba cualquier cliente, la app de Meta tiene que estar en modo
<b>Live</b>.</li>
</ol>"""


def whatsapp_guide(*, english: bool | None = None) -> str:
    use_english = panel_language.english() if english is None else english
    return f'<div translate="no">{WHATSAPP_EN if use_english else WHATSAPP_ES}</div>'
