"""Deploy (Hito 13, tarea 4; decisión 086): una sola pantalla guiada, con el estado de cada paso.

1. Conectar la tienda: Shopify (decisión 067), Tiendanube u otra con su API. Conectar prueba la
   conexión en el momento y dice qué falta (un permiso, una credencial o la tienda).
2. Qué acciones puede hacer el agente (con un link a Agent → Actions para los límites).
3. Instalar el chat en el sitio: los dominios de la tienda se agregan solos, el código para
   copiar, guías por plataforma y "Check installation".
4. WhatsApp (decisión 068).
5. Salir en vivo, con el reporte de preparación, y pausar.

Los formularios de los pasos 1 y 4 son los de antes (`/panel/configuracion/...`).
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from app.channels.web import widget
from app.channels.whatsapp import selfservice as whatsapp
from app.companies import connection as system
from app.companies import deploy, go_live, review
from app.companies.create import chat_slug
from app.connectors import shopify_auth
from app.panel import guides
from app.panel.company_routes import (
    LEADS,
    _back,
    _csrf,
    _notice,
    _public_url,
    _whatsapp_html,
    head,
)
from app.panel.html import esc, page
from app.panel.labels import tool_label
from app.panel.live_routes import readiness_html
from app.panel.routes import Ctx, Deps, Form, enter

router = APIRouter(prefix="/panel", include_in_schema=False)

TITLES = {
    "store": ("Connect your store", "So your agent can look up orders, products and customers."),
    "actions": ("Choose what your agent can do", "Turn on the actions it can take in your store."),
    "website": ("Add the chat to your website", "Paste one line of code on your site."),
    "whatsapp": ("Connect WhatsApp", "Optional: the same agent answers on your WhatsApp number."),
    "live": ("Go live", "When everything is ready, your agent starts answering customers."),
}
# Copiar el código: con el portapapeles o, si no hay, seleccionándolo.
COPY_JS = (
    "(function(){var b=document.getElementById('copy-snippet');if(!b)return;"
    "b.addEventListener('click',function(){var p=document.getElementById('snippet');"
    "(navigator.clipboard?navigator.clipboard.writeText(p.textContent):Promise.reject())"
    ".then(function(){b.textContent=b.getAttribute('data-done')},function(){"
    "var r=document.createRange();r.selectNodeContents(p);var s=getSelection();"
    "s.removeAllRanges();s.addRange(r)})})})();"
)
BADGES = {"done": "ok", "progress": "warn", "todo": "", "optional": "info"}
SHOPIFY_NOTE = "Orders, refunds, coupons, cancellations and products"
API_NOTE = "Any system with an OpenAPI description"
PLATFORM_NAMES = {"shopify": "Shopify", "tiendanube": "Tiendanube", "api": "Your system (API)"}

DEPLOY_CSS = """<style>
.dstep{padding:0;overflow:hidden}.dstep>header{display:flex;gap:12px;align-items:center;
padding:16px 18px}.dstep>header .num{width:28px;height:28px;border-radius:50%;flex:none;
display:grid;place-items:center;font-weight:700;font-size:13px;background:var(--sunken);
color:var(--muted)}.dstep.done>header .num{background:var(--ok-soft);color:var(--ok)}
.dstep>header h2{margin:0;font-size:15.5px}.dstep>header p{margin:2px 0 0}
.dstep>.body{padding:0 18px 18px 58px}
@media (max-width:640px){.dstep>.body{padding:0 16px 16px}}
.choices{display:grid;gap:10px}.choice{border:1px solid var(--line);border-radius:10px}
.choice>summary{list-style:none;display:flex;gap:10px;align-items:center;padding:12px 14px;
font-weight:600}.choice>summary::-webkit-details-marker{display:none}
.choice>summary::after{content:"›";margin-left:auto;color:var(--muted);font-size:18px}
.choice[open]>summary{border-bottom:1px solid var(--line)}.choice[open]>summary::after{
transform:rotate(90deg)}.choice>.inner{padding:4px 14px 14px}
.choice small{font-weight:400;color:var(--muted)}
ol.guide{padding-left:20px;margin:8px 0}ol.guide li{margin:6px 0}
.diag{border-radius:10px;padding:12px 14px;margin:12px 0;border:1px solid var(--line)}
.diag.bad{background:var(--bad-soft);border-color:var(--bad)}
.diag.good{background:var(--ok-soft);border-color:var(--ok)}
.diag dt{font-weight:700;margin-top:4px}.diag dd{margin:0 0 4px}
.snippet{display:flex;gap:8px;align-items:stretch;flex-wrap:wrap}.snippet pre{flex:1;margin:0;
min-width:0;white-space:pre-wrap;word-break:break-all}
.acts{list-style:none;padding:0;margin:8px 0}.acts li{display:flex;gap:10px;align-items:center;
padding:8px 0;border-top:1px solid var(--line)}
</style>"""


def _step(number: int, step: deploy.Step, body: str) -> str:
    title, lead = TITLES[step.key]
    mark = "✓" if step.state == "done" else str(number)
    return f"""<section class="card dstep {step.state}" id="{step.key}"
 aria-labelledby="{step.key}-title"><header><span class="num" aria-hidden="true">{mark}</span>
<div class="grow"><h2 id="{step.key}-title"><span class="muted">{number}.</span> {title}</h2>
<p class="muted small">{lead}</p></div>
<span class="badge {BADGES[step.state]}">{esc(step.label)}</span></header>
<div class="body">{body}</div></section>"""


# -- paso 1: la tienda ---------------------------------------------------------------------------
def _diagnosis_html(diagnosis: deploy.Diagnosis | None) -> str:
    if diagnosis is None:
        return ""
    if diagnosis.ok:
        return (
            '<div class="diag good" role="status"><span aria-hidden="true">✓</span> '
            f"<b>{esc(diagnosis.what)}</b></div>"
        )
    missing = {
        deploy.PERMISSION: "A permission",
        deploy.CREDENTIAL: "A credential",
        deploy.STORE: "The store",
        deploy.SYSTEM: "Your system",
    }[diagnosis.missing or deploy.SYSTEM]
    return f"""<div class="diag bad" role="alert"><b>The connection test failed.</b><dl>
<dt>What's missing</dt><dd><b>{missing}</b><br>{esc(diagnosis.what)}</dd>
<dt>What to do</dt><dd>{esc(diagnosis.todo)}</dd></dl></div>"""


def _shopify_form(ctx: Ctx) -> str:
    oauth = ""
    if shopify_auth.app_credentials():
        oauth = f"""<form method="post" action="/panel/configuracion/sistema/shopify/instalar"
 class="row" style="align-items:flex-end;margin-top:14px">{_csrf(ctx)}
<div class="grow"><label for="oauth-tienda">Store address</label><input id="oauth-tienda"
 name="tienda" required placeholder="yourstore.myshopify.com"></div>
<button class="primary" type="submit">Install with Shopify</button></form>
<p class="help">The fastest way: Shopify asks you to approve our app and you come back here
connected. Or connect your own app below.</p>"""
    return f"""{oauth}
<form method="post" action="/panel/configuracion/sistema/plataforma">{_csrf(ctx)}
<input type="hidden" name="plataforma" value="shopify">
<label for="shop-tienda">Store address</label>
<input id="shop-tienda" name="tienda" required placeholder="yourstore.myshopify.com">
<p class="example">Example: acme-store.myshopify.com</p>
<fieldset style="border:0;padding:0;margin:12px 0 0"><legend class="small"><b>How do you want
to connect?</b></legend>
<label style="font-weight:400"><input type="radio" name="metodo" value="app" checked>
App from the Dev Dashboard: Client ID and Client secret (recommended)</label>
<label style="font-weight:400;margin-top:4px"><input type="radio" name="metodo" value="token">
Access token of a legacy app your store already has (shpat_…)</label></fieldset>
<div class="grid2"><div><label for="client_id">Client ID</label>
<input id="client_id" name="client_id" autocomplete="off"></div>
<div><label for="client_secret">Client secret</label>
<input id="client_secret" name="client_secret" type="password" autocomplete="off"></div></div>
<label for="shop-token">Access token (legacy app only)</label>
<input id="shop-token" name="token" type="password" autocomplete="off">
<p style="margin-top:12px"><button class="primary" type="submit">Connect</button></p></form>
<details><summary>How to get the Client ID and secret</summary>{guides.shopify_app_guide()}
</details>"""


def _tiendanube_form(ctx: Ctx) -> str:
    return f"""<form method="post" action="/panel/configuracion/sistema/plataforma">{_csrf(ctx)}
<input type="hidden" name="plataforma" value="tiendanube">
<div class="grid2"><div><label for="tn-tienda">Store number</label>
<input id="tn-tienda" name="tienda" required inputmode="numeric" placeholder="123456"></div>
<div><label for="tn-token">Access token</label><input id="tn-token" name="token"
 type="password" autocomplete="off" required></div></div>
<p style="margin-top:12px"><button class="primary" type="submit">Connect</button></p></form>
<details><summary>How to get them</summary>{guides.tiendanube_guide()}</details>"""


def _api_form(ctx: Ctx, found: system.SystemConnection | None) -> str:
    kind = found.auth_kind if found else "none"
    options = "".join(
        f'<option value="{value}"{" selected" if value == kind else ""}>{text}</option>'
        for value, text in (
            ("none", "No authentication"),
            ("bearer", "Token (Authorization: Bearer)"),
            ("api_key", "API key in a header"),
            ("basic", "User and password (Basic)"),
        )
    )
    spec = (
        f"Your API description is loaded: {found.operations} operations."
        if found and found.openapi
        else "Upload your API description (OpenAPI, .json or .yaml)."
    )
    saved = "Saved. Leave it empty to keep it." if found and found.has_credentials else ""
    return f"""<p class="help">For developers: your agent's actions are created from your API
description.</p>
<form method="post" action="/panel/configuracion/sistema/openapi" enctype="multipart/form-data">
{_csrf(ctx)}<label for="archivo">{esc(spec)}</label>
<input id="archivo" name="archivo" type="file" accept=".json,.yaml,.yml">
<textarea name="texto" class="mono" aria-label="Or paste it here"
 placeholder="…or paste it here"></textarea>
<button type="submit">Save API description</button></form>
<form method="post" action="/panel/configuracion/sistema/acceso" style="margin-top:14px">
{_csrf(ctx)}<label for="url">Base URL (HTTPS)</label>
<input id="url" name="url" type="url" required placeholder="https://api.yourcompany.com"
 value="{esc(found.base_url if found and found.base_url else "")}">
<div class="grid2"><div><label for="auth">Authentication</label>
<select id="auth" name="auth">{options}</select></div>
<div><label for="encabezado">Header name (API key only)</label>
<input id="encabezado" name="encabezado" placeholder="X-API-Key"
 value="{esc(found.auth_header if found and found.auth_header else "")}"></div></div>
<label for="credencial">Credential (token, API key or user:password)</label>
<input id="credencial" name="credencial" type="password" autocomplete="off"
 placeholder="{esc(saved)}">
<p class="help">Stored encrypted and never shown again.</p>
<button class="primary" type="submit">Save</button></form>"""


def _choices(ctx: Ctx, found: system.SystemConnection | None, open_key: str = "") -> str:
    def choice(key: str, title: str, note: str, body: str) -> str:
        opened = " open" if key == open_key else ""
        return f"""<details class="choice" name="platform"{opened}><summary>{title}
<small>{note}</small></summary><div class="inner">{body}</div></details>"""

    return f"""<div class="choices">
{choice("shopify", "Shopify", SHOPIFY_NOTE, _shopify_form(ctx))}
{choice("tiendanube", "Tiendanube", "Orders, coupons and products", _tiendanube_form(ctx))}
{choice("api", "Other platform (API)", API_NOTE, _api_form(ctx, found))}
</div>"""


def _store_body(ctx: Ctx, found: system.SystemConnection | None, mode: str) -> str:
    if mode == "demo":
        return '<p class="muted">This demo company uses a simulated store with test orders.</p>'
    platform = deploy.platform_of(found)
    if not platform or found is None or not found.base_url:
        return _choices(ctx, found)
    address = (found.base_url or "").split(":", 1)[-1] if platform != "api" else found.base_url
    diagnosis = _diagnosis_html(deploy.explain(found.last_test, platform))
    if found.status == "active":
        state = "Your agent uses your real store."
        toggle = f"""<form method="post" action="/panel/configuracion/sistema/desactivar"
 data-confirm="Go back to test mode? Your agent stops using your real store.">
{_csrf(ctx)}<button type="submit">Back to test mode</button></form>"""
    else:
        state = (
            "Test mode: until you turn it on, your agent uses made-up answers and never "
            "touches your store."
        )
        disabled = "" if found.status == "tested" else " disabled"
        toggle = f"""<form method="post" action="/panel/configuracion/sistema/activar">
{_csrf(ctx)}<button class="primary" type="submit"{disabled}>Use my real store</button></form>"""
    return f"""<p><b>{PLATFORM_NAMES[platform]}</b> · <span class="mono">{esc(address)}</span></p>
<p class="muted small">{state}</p>{diagnosis}
<div class="row"><form method="post" action="/panel/configuracion/sistema/probar">{_csrf(ctx)}
<button type="submit">Test connection</button></form>{toggle}</div>
<details style="margin-top:12px"><summary>Connect a different store</summary>
{_choices(ctx, found, platform)}</details>"""


# -- paso 2: acciones ------------------------------------------------------------------------------
def _actions_body(tools: list[review.Tool]) -> str:
    if not tools:
        return (
            '<p class="muted">Your agent\'s actions come from your store. Connect it in step 1 '
            "and they show up here.</p>"
        )
    items = "".join(
        f"""<li><span class="grow">{esc(tool_label(t.name))}</span>
<span class="badge {"ok" if t.approved else ""}">{"On" if t.approved else "Off"}</span></li>"""
        for t in tools
    )
    return f"""<p class="muted small">Nothing runs until you turn it on. High-risk actions, like
a refund, always wait for a person's approval.</p><ul class="acts">{items}</ul>
<a class="btn primary" href="/panel/agent/actions">Choose actions and limits</a>"""


# -- paso 3: el sitio ------------------------------------------------------------------------------
def _website_body(
    ctx: Ctx, request: Request, slug: str | None, found: widget.WidgetSettings
) -> str:
    if not slug:
        return '<p class="muted">This company has no web chat.</p>'
    code = widget.snippet(_public_url(request), slug)
    tabs = "".join(
        f"""<details class="choice" name="install"><summary>{name}</summary>
<div class="inner">{guides.install_guide(key)}</div></details>"""
        for key, name in guides.INSTALL_NAMES
    )
    domains = "\n".join(found.domains)
    return f"""<h3 style="margin-top:0">Your code</h3>
<div class="snippet"><pre id="snippet" class="mono">{esc(code)}</pre>
<button type="button" id="copy-snippet" data-done="Copied">Copy code</button></div>
<h3>Where to paste it</h3><div class="choices">{tabs}</div>
<form method="post" action="/panel/deploy/domains" style="margin-top:14px">{_csrf(ctx)}
<label for="dominios">Allowed domains</label>
<textarea id="dominios" name="dominios" class="mono" style="min-height:70px"
 placeholder="www.yourstore.com">{esc(domains)}</textarea>
<p class="help">The chat only works on these sites, one per line. Your connected store's domains
are added automatically. Use *.yourstore.com for every subdomain.</p>
<button type="submit">Save domains</button></form>
<form method="post" action="/panel/deploy/check-install" class="row" style="margin-top:14px">
{_csrf(ctx)}<button class="primary" type="submit">Check installation</button>
<a class="small" href="/c/{esc(slug)}" target="_blank" rel="noopener">Open your chat page</a>
</form><script>{COPY_JS}</script>"""


@router.get("/deploy", response_class=HTMLResponse)
def deploy_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        status = deploy.steps(conn, ctx.tenant_id)
        found = system.load(conn, ctx.tenant_id)
        mode = system.mode_for(conn, ctx.tenant_id)
        tools = review.tools(conn, ctx.tenant_id)
        slug = chat_slug(conn, ctx.tenant_id)
        brand = widget.load(conn, ctx.tenant_id)
        whatsapp_setup = whatsapp.load(conn, ctx.tenant_id)
        readiness = go_live.readiness(conn, ctx.tenant_id)
    by_key = {s.key: s for s in status}
    bodies = {
        "store": _store_body(ctx, found, mode),
        "actions": _actions_body(tools),
        "website": _website_body(ctx, request, slug, brand),
        "whatsapp": _whatsapp_html(ctx, request, whatsapp_setup),
        "live": readiness_html(ctx, readiness),
    }
    steps = "".join(
        _step(number, by_key[key], bodies[key])
        for number, key in enumerate(("store", "actions", "website", "whatsapp", "live"), 1)
    )
    done = sum(s.state == "done" for s in status if s.key != "whatsapp")
    body = f"""{DEPLOY_CSS}{head("Deploy", LEADS["Deploy"])}
<p class="muted small" style="margin-top:-8px">{done} of 4 required steps done</p>
{_notice(request)}{steps}"""
    return HTMLResponse(page("Deploy", body, active="deploy", session=ctx.session))


# -- acciones del paso 3 -------------------------------------------------------------------------
@router.post("/deploy/domains")
def save_domains(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            widget.save_domains(conn, ctx.tenant_id, form.get("dominios", ""))
    except widget.WidgetError as error:
        return _back("deploy", error=str(error))
    return _back("deploy", ok="Domains saved.")


CHECK = {
    "loaded": ("ok", "Your chat is installed: it loaded from {domain}."),
    "found": (
        "warning",
        "We found the code on {domain}, but the chat hasn't loaded there yet. Open your site in "
        "a browser and check again.",
    ),
    "missing": (
        "error",
        "We couldn't find the code on {domain}. Paste it right before </body> and publish your "
        "site.",
    ),
    "unreachable": (
        "error",
        "We couldn't open {domain}. Check that the domain is right and your site is online.",
    ),
    "no_domains": ("error", "Add your website's domain under Allowed domains first."),
}


@router.post("/deploy/check-install")
async def check_install(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx

    def run() -> deploy.InstallCheck:
        with deps.connection() as conn:
            slug = chat_slug(conn, ctx.tenant_id) or ""
            return deploy.check_install(conn, ctx.tenant_id, slug)

    result = await run_in_threadpool(run)
    kind, text = CHECK[result.status]
    message = text.format(domain=result.domain)
    return _back("deploy", **{kind: message})
