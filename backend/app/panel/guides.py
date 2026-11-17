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
        "asistente atiende ahí. Lo conecta el operador: escribinos.</p>",
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
        "can answer there. The operator connects it: write to us.</p>",
    ),
)


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
