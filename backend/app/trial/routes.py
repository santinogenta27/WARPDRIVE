"""Páginas públicas de la prueba gratis (decisión 075).

- `GET /probar` (o `/try`, en inglés): la URL de la tienda y el email.
- `POST /probar`: valida, aplica los límites y manda el código.
- `GET /probar/{token}`: según el estado, el código, el avance o la prueba lista: el chat con
  "detrás de escena", los pedidos de ejemplo, sugerencias, el mini panel de aprobaciones y los
  botones para pasar a piloto o hablar con el operador.
- `GET /probar/{token}/estado`, `/aprobaciones`: lo que la página consulta.

El token del enlace es la credencial de la prueba: solo se guarda su hash.
"""

import hmac
import html
import json
import os
import time
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import i18n
from app.channels.web.routes import _ip_hash, client_ip
from app.db.connection import tenant_scope
from app.panel import auth
from app.panel.notify import notify_customer
from app.panel.routes import Deps, Form
from app.policy.approvals import ApprovalError, decide
from app.trial import service

router = APIRouter(include_in_schema=False)
ES_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
EN_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MIN_FORM_SECONDS = 3  # un bot completa el formulario en menos
MAX_FORM_SECONDS = 2 * 3600

T: dict[str, dict[str, str]] = {
    "es": {
        "title": "Probalo con tu tienda",
        "trial_tag": "Prueba gratis",
        "lead": (
            "Escribí la dirección de tu tienda y tu email. En unos minutos tenés el mismo "
            "asistente que vendemos, con tus políticas y tus productos. No toca tu tienda real."
        ),
        "site": "Dirección de tu tienda",
        "placeholder": "tutienda.com",
        "email": "Tu email",
        "start": "Armar mi prueba",
        "fine": "Una prueba por tienda y por email. Se borra sola a los 7 días.",
        "code_title": "Revisá tu email",
        "code_lead": "Te mandamos un código de 6 dígitos a {email}. Escribilo para seguir.",
        "code": "Código",
        "confirm": "Confirmar",
        "building": "Estamos armando tu prueba",
        "step_crawl": "Leyendo las páginas principales de tu sitio",
        "step_index": "Aprendiendo tus políticas y tus productos",
        "step_agent": "Configurando tu asistente con una tienda de ejemplo",
        "read_of": "Leímos {read} de {total} páginas",
        "read_done": "Leímos {n} páginas",
        "reading": "Leyendo {page}",
        "elapsed": "Tiempo",
        "found_products": "Productos que encontramos",
        "found_policies": "Políticas que encontramos",
        "calm": (
            "Suele tardar menos de un minuto. Podés dejar esta pestaña abierta; también te "
            "avisamos por email cuando esté listo."
        ),
        "stale": "Está tardando más de lo normal. Podés reintentar: no se duplica nada.",
        "retry": "Reintentar",
        "failed": "No pudimos armar tu prueba",
        "policy_shipping": "Envíos",
        "policy_returns": "Devoluciones",
        "policy_warranty": "Garantía",
        "policy_payments": "Pagos",
        "policy_faq": "Preguntas frecuentes",
        "policy_privacy": "Privacidad",
        "policy_terms": "Términos",
        "policy_contact": "Contacto",
        "error_unreachable": (
            "No pudimos abrir {domain}. Revisá que la dirección esté bien y que el sitio sea "
            "público, y probá de nuevo."
        ),
        "error_blocked": (
            "{domain} no nos dejó leer sus páginas. Algunas tiendas bloquean las visitas "
            "automáticas: probá de nuevo o escribinos y la armamos nosotros."
        ),
        "error_empty": (
            "Abrimos {domain} pero no encontramos texto para leer. Si tu tienda se arma solo con "
            "JavaScript, escribinos y la armamos nosotros."
        ),
        "error_unsafe": "La dirección {domain} no es un sitio público.",
        "error_internal": (
            "Algo falló de nuestro lado mientras armábamos tu prueba. Probá de nuevo; si vuelve a "
            "pasar, te ayudamos."
        ),
        "ready_lead": (
            "Este es el mismo asistente que vendemos, con las políticas de tu sitio y una tienda "
            "de ejemplo con tus productos. Junto a la conversación ves qué hace por "
            "dentro. Nada de esto toca tu tienda real."
        ),
        "orders": "Pedidos de ejemplo",
        "orders_note": (
            "Son pedidos inventados con tus productos, para probar. Para verlos, el asistente "
            "te pide verificarte: usá tu email ({email}), te llega un código."
        ),
        "tries": "Probá estos mensajes",
        "approvals": "Aprobaciones",
        "approvals_note": (
            "Los reembolsos no se ejecutan hasta que una persona los aprueba. En tu piloto lo hace "
            "tu equipo desde el panel; acá lo hacés vos."
        ),
        "none": "No hay nada esperando aprobación.",
        "approve": "Aprobar",
        "reject": "Rechazar",
        "pilot": "Empezar mi piloto",
        "talk": "Hablar con Santino",
        "talked": "Listo: Santino te escribe a {email}.",
        "products": "Tus productos",
        "generic": "No encontramos productos en tu sitio: usamos productos de ejemplo.",
        "left": "Mensajes de la prueba: {left} de {total}",
        "converted": "Esta prueba ya es tu piloto",
        "converted_lead": "Entrá al panel con el email y la clave que elegiste.",
        "login": "Ir al panel",
        "expired": "Esta prueba venció. Si querés seguir, escribinos.",
        "statuses": json.dumps(
            {
                "shipped": "En camino",
                "delivered": "Entregado",
                "paid": "Sin despachar",
                "cancelled": "Cancelado",
            }
        ),
        "delay": "Demora",
        "tracking": "Seguimiento",
    },
    "en": {
        "title": "Try it with your store",
        "trial_tag": "Free trial",
        "lead": (
            "Enter your store's address and your email. In a few minutes you get the same "
            "assistant we sell, with your policies and your products. It doesn't touch your real "
            "store."
        ),
        "site": "Your store's address",
        "placeholder": "yourstore.com",
        "email": "Your email",
        "start": "Set up my trial",
        "fine": "One trial per store and per email. It deletes itself after 7 days.",
        "code_title": "Check your email",
        "code_lead": "We sent a 6-digit code to {email}. Enter it to continue.",
        "code": "Code",
        "confirm": "Confirm",
        "building": "We're setting up your trial",
        "step_crawl": "Reading your site's main pages",
        "step_index": "Learning your policies and products",
        "step_agent": "Setting up your assistant with a sample store",
        "read_of": "Read {read} of {total} pages",
        "read_done": "Read {n} pages",
        "reading": "Reading {page}",
        "elapsed": "Elapsed",
        "found_products": "Products we found",
        "found_policies": "Policies we found",
        "calm": (
            "This usually takes under a minute. You can keep this tab open; we'll also email you "
            "when it's ready."
        ),
        "stale": "This is taking longer than usual. You can try again: nothing gets duplicated.",
        "retry": "Try again",
        "failed": "We couldn't set up your trial",
        "policy_shipping": "Shipping",
        "policy_returns": "Returns",
        "policy_warranty": "Warranty",
        "policy_payments": "Payments",
        "policy_faq": "FAQ",
        "policy_privacy": "Privacy",
        "policy_terms": "Terms",
        "policy_contact": "Contact",
        "error_unreachable": (
            "We couldn't open {domain}. Check that the address is right and that the site is "
            "public, then try again."
        ),
        "error_blocked": (
            "{domain} didn't let us read its pages. Some stores block automated visits: try "
            "again, or write to us and we'll set it up for you."
        ),
        "error_empty": (
            "We opened {domain} but found no text to read. If your store is built only with "
            "JavaScript, write to us and we'll set it up for you."
        ),
        "error_unsafe": "The address {domain} isn't a public site.",
        "error_internal": (
            "Something went wrong on our side while setting up your trial. Try again; if it "
            "happens again, we'll help you."
        ),
        "ready_lead": (
            "This is the same assistant we sell, with your site's policies and a sample store "
            "with your products. Next to the conversation you see what it does behind the "
            "scenes. None of this touches your real store."
        ),
        "orders": "Sample orders",
        "orders_note": (
            "These are made-up orders with your products, to try it out. To see them, the "
            "assistant asks you to verify: use your email ({email}) and you'll get a code."
        ),
        "tries": "Try these messages",
        "approvals": "Approvals",
        "approvals_note": (
            "Refunds don't run until a person approves them. In your pilot your team does it from "
            "the dashboard; here, you do."
        ),
        "none": "Nothing is waiting for approval.",
        "approve": "Approve",
        "reject": "Reject",
        "pilot": "Start my pilot",
        "talk": "Talk to Santino",
        "talked": "Done: Santino will write to you at {email}.",
        "products": "Your products",
        "generic": "We didn't find products on your site: we used sample products.",
        "left": "Trial messages: {left} of {total}",
        "converted": "This trial is already your pilot",
        "converted_lead": "Log in to the dashboard with the email and password you chose.",
        "login": "Go to the dashboard",
        "expired": "This trial expired. Write to us if you want to continue.",
        "statuses": json.dumps(
            {
                "shipped": "On its way",
                "delivered": "Delivered",
                "paid": "Not shipped yet",
                "cancelled": "Cancelled",
            }
        ),
        "delay": "Delay",
        "tracking": "Tracking",
    },
}

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d2421;--muted:#5c6862;--line:#e2e5e1;--accent:#2f6b57;
--accent-ink:#fff;--warn:#9a5b00;--ok:#2f6b57}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121715;--card:#1a211e;
--ink:#e8ede9;--muted:#a4b0a9;--line:#2e3833;--accent:#7cc2a6;--accent-ink:#0d1411;
--warn:#e2a54b;--ok:#7cc2a6}}
:root[data-theme="dark"]{--bg:#121715;--card:#1a211e;--ink:#e8ede9;--muted:#a4b0a9;
--line:#2e3833;--accent:#7cc2a6;--accent-ink:#0d1411;--warn:#e2a54b;--ok:#7cc2a6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,
"Segoe UI",sans-serif}
main{max-width:1240px;margin:0 auto;padding:24px 16px 48px}
.narrow{max-width:480px;margin:48px auto}
h1{font-size:28px;line-height:1.2;margin:0 0 8px}
h2{font-size:17px;margin:0 0 10px}
.lead{color:var(--muted);margin:0 0 20px}
.top .lead{max-width:760px;margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;
margin-bottom:16px}
label{display:block;font-weight:600;font-size:14px;margin:14px 0 6px}
input{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--bg);color:var(--ink);font:inherit}
button,.button{display:inline-block;padding:11px 16px;border-radius:10px;border:1px solid
var(--accent);background:var(--accent);color:var(--accent-ink);font:inherit;font-weight:600;
cursor:pointer;text-decoration:none}
.ghost{background:transparent;color:var(--accent)}
.full{width:100%;margin-top:18px}
.muted{color:var(--muted)}.small{font-size:14px}
.error{color:var(--warn);font-weight:600}
.trap{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}
.top{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between;
margin-bottom:16px}
.actions{display:flex;flex-wrap:wrap;gap:8px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
@media (max-width:900px){.grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){main iframe{height:1180px}}
iframe{width:100%;height:780px;border:1px solid var(--line);border-radius:14px;
background:var(--card)}
.tag{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.04em;
text-transform:uppercase;color:var(--warn);border:1px solid currentColor;border-radius:999px;
padding:1px 8px}
.order{border-top:1px solid var(--line);padding:10px 0}
.order:first-of-type{border-top:0}
.row{display:flex;justify-content:space-between;gap:8px}
ul.tries{list-style:none;padding:0;margin:0}
ul.tries li{margin:0 0 6px}
ul.tries button{width:100%;text-align:left;background:var(--bg);color:var(--ink);
border-color:var(--line);font-weight:500}
.steps{list-style:none;padding:0;margin:0}
.steps li{padding:8px 0;color:var(--muted)}
.steps li.on{color:var(--ink);font-weight:600}
.steps li.done{color:var(--ok)}
.products{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}
.products figure{margin:0;min-width:96px;max-width:96px;font-size:12px}
.products img{width:96px;height:96px;object-fit:cover;border-radius:10px;
border:1px solid var(--line);background:var(--bg)}
.approval{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);
padding:10px 0}
.approval .actions button{padding:7px 12px}
"""

# La pantalla de armado y la de falla, con los tokens del panel (decisiones 089 y 099).
BUILD_CSS = """
.build{--brand:#4338ca;--brand-soft:#eef0fd;--on-brand:#fff;--ok:#157f3c;--bad:#c02626;
--bad-soft:#fdecec;--sunken:#f1f2f4;--ink-2:#3c414b;max-width:680px;margin:24px auto}
@media (prefers-color-scheme:dark){.build{--brand:#8b93f8;--brand-soft:#20233d;
--on-brand:#0e1014;--ok:#4ade80;--bad:#f87171;--bad-soft:#341617;--sunken:#1c2029;
--ink-2:#c3c8d1}}
.build .card{padding:22px;border-radius:10px;box-shadow:0 1px 2px rgba(16,24,40,.06)}
.build h1{font-size:24px}.build h2{font-size:15px;margin:22px 0 8px}
.build button{background:var(--brand);border-color:var(--brand);color:var(--on-brand)}
.build button.ghost{background:transparent;color:var(--brand)}
.build :focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.build .site{color:var(--muted);font-size:14px;margin:0;overflow-wrap:anywhere}
.bar{height:8px;border-radius:999px;background:var(--sunken);overflow:hidden;margin:18px 0 6px}
.bar>span{display:block;height:100%;width:3%;background:var(--brand);border-radius:inherit;
transition:width .5s ease}
.meta{display:flex;justify-content:space-between;gap:8px;font-size:13px;color:var(--muted);
font-variant-numeric:tabular-nums}
.plan{list-style:none;margin:14px 0 0;padding:0}
.plan li{display:grid;grid-template-columns:28px minmax(0,1fr) auto;gap:10px;
align-items:start;padding:10px 0;border-top:1px solid var(--line)}
.plan li:first-child{border-top:0}
.dot{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:13px;
font-weight:700;border:2px solid var(--line);color:var(--muted);box-sizing:border-box}
.plan li.on .dot{border-color:var(--brand);border-top-color:transparent;
animation:spin 1s linear infinite}
.plan li.done .dot{background:var(--ok);border-color:var(--ok);color:var(--on-brand)}
.what{font-weight:600}.plan li:not(.on):not(.done) .what{color:var(--muted)}
.detail{font-size:13px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.took{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.plan li.on .dot{animation:none;
border-top-color:var(--brand)}.bar>span{transition:none}}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:0;padding:0;list-style:none}
.chips li{font-size:13px;padding:3px 10px;border-radius:999px;background:var(--brand-soft)}
.found{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:12px}
.found figure{margin:0;font-size:13px;line-height:1.3}
.found .ph,.found img{display:block;width:100%;aspect-ratio:1;object-fit:cover;
border-radius:10px;border:1px solid var(--line);background:var(--sunken)}
.found figcaption{margin-top:6px;overflow-wrap:anywhere}.found .price{color:var(--muted)}
@media (max-width:480px){.build{margin:8px auto}.build .card{padding:16px}
.found{grid-template-columns:repeat(2,minmax(0,1fr))}}
.calm{margin:20px 0 0;padding:12px 14px;border-radius:10px;background:var(--sunken);
font-size:14px;color:var(--ink-2)}
.notice{margin:16px 0 0;padding:12px 14px;border-radius:10px;background:var(--bad-soft);
font-size:14px}
.notice .actions{margin-top:10px}
"""


def _lang(value: str | None) -> str:
    return "en" if value == "en" else "es"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _layout(
    title: str, body: str, language: str, status: int = 200, extra_css: str = ""
) -> HTMLResponse:
    markup = (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{esc(title)}</title>'
        f"<style>{CSS}{extra_css}</style></head><body><main>{body}</main></body></html>"
    )
    return HTMLResponse(markup, status_code=status, headers={"Cache-Control": "no-store"})


def form_stamp(moment: float) -> str:
    """La hora en que se abrió el formulario, firmada: así se sabe cuánto tardó en enviarse."""
    seconds = str(int(moment))
    return f"{seconds}.{auth._sign('probar:' + seconds)[:24]}"


def _human(stamp: str) -> bool:
    seconds, _, signature = stamp.partition(".")
    if not seconds.isdigit() or not hmac.compare_digest(
        signature, auth._sign("probar:" + seconds)[:24]
    ):
        return False
    elapsed = time.time() - int(seconds)
    return MIN_FORM_SECONDS <= elapsed <= MAX_FORM_SECONDS


def _form_page(
    language: str, error: str = "", values: dict[str, str] | None = None
) -> HTMLResponse:
    t = T[language]
    v = values or {}
    alert = f'<p class="error" role="alert">{esc(error)}</p>' if error else ""
    body = f"""<div class="narrow"><div class="card">
<h1>{esc(t["title"])}</h1><p class="lead">{esc(t["lead"])}</p>{alert}
<form method="post" action="/probar">
<input type="hidden" name="t" value="{esc(form_stamp(time.time()))}">
<input type="hidden" name="lang" value="{language}">
<div class="trap" aria-hidden="true"><label for="web">Web</label>
<input id="web" name="web" tabindex="-1" autocomplete="off"></div>
<label for="sitio">{esc(t["site"])}</label>
<input id="sitio" name="sitio" required maxlength="300" placeholder="{esc(t["placeholder"])}"
 value="{esc(v.get("sitio", ""))}" autocomplete="url">
<label for="email">{esc(t["email"])}</label>
<input id="email" name="email" type="email" required maxlength="200"
 value="{esc(v.get("email", ""))}" autocomplete="email">
<button class="full" type="submit">{esc(t["start"])}</button></form>
<p class="muted small">{esc(t["fine"])}</p></div></div>"""
    return _layout(t["title"], body, language, 400 if error else 200)


def _prefill(sitio: str, email: str) -> dict[str, str]:
    """Datos que llegan del formulario del sitio web (enterprisedataai.com): solo completan los
    campos. El envío sigue pasando por este formulario, con su sello de tiempo y su trampa."""
    return {"sitio": sitio.strip()[:300], "email": email.strip()[:200]}


@router.get("/probar", response_class=HTMLResponse)
def trial_form(lang: str = "", sitio: str = "", email: str = "") -> HTMLResponse:
    return _form_page(_lang(lang), values=_prefill(sitio, email))


@router.get("/try", response_class=HTMLResponse)
def trial_form_en(sitio: str = "", email: str = "") -> HTMLResponse:
    return _form_page("en", values=_prefill(sitio, email))


@router.post("/probar")
def trial_start(request: Request, form: Form, deps: Deps) -> Response:
    language = _lang(form.get("lang"))
    values = {k: form.get(k, "") for k in ("sitio", "email")}
    if form.get("web") or not _human(form.get("t", "")):
        # Un bot: la misma respuesta de error, sin decir por qué.
        return _form_page(language, service.ERRORS[language]["site"], values)
    peer = request.client.host if request.client else None
    mode = os.environ.get("WEB_CHAT_CLIENT_IP") or "direct"
    ip_hash = _ip_hash(client_ip(mode, request.headers, peer))
    try:
        with deps.connection() as conn:
            token = service.start(
                conn,
                deps.mailer(),
                site=values["sitio"],
                email=values["email"],
                language=language,
                ip_hash=ip_hash,
            )
    except service.TrialError as error:
        return _form_page(language, str(error), values)
    return RedirectResponse(f"/probar/{token}", status_code=303)


def _code_page(trial: service.Trial, token: str, error: str = "") -> HTMLResponse:
    t = T[trial.language]
    alert = f'<p class="error" role="alert">{esc(error)}</p>' if error else ""
    body = f"""<div class="narrow"><div class="card">
<h1>{esc(t["code_title"])}</h1><p class="lead">{esc(t["code_lead"].format(email=trial.email))}</p>
{alert}<form method="post" action="/probar/{esc(token)}/codigo">
<label for="codigo">{esc(t["code"])}</label>
<input id="codigo" name="codigo" required inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6"
 autocomplete="one-time-code">
<button class="full" type="submit">{esc(t["confirm"])}</button></form></div></div>"""
    return _layout(t["code_title"], body, trial.language, 400 if error else 200)


STEPS = ("crawl", "index", "agent")

# La pantalla de armado: consulta el estado cada 1,5 s y pinta todo con textContent (lo que
# viene del sitio del visitante es dato, nunca HTML).
BUILD_JS = """
(function(){
  var c=JSON.parse(document.getElementById("cfg").textContent), t=c.t;
  var $=function(id){return document.getElementById(id)};
  var base=0, at=Date.now(), steps=["crawl","index","agent"];
  function fill(s,v){return s.replace(/\\{(\\w+)\\}/g,function(_,k){return v[k]})}
  function clock(n){n=Math.max(0,Math.floor(n));return Math.floor(n/60)+":"+("0"+n%60).slice(-2)}
  function money(v,cur){
    try{return new Intl.NumberFormat(c.lang==="en"?"en-US":"es-AR",{style:"currency",
      currency:cur||"USD",maximumFractionDigits:v%1?2:0}).format(v)}
    catch(e){return (cur||"")+" "+v}
  }
  function path(u){try{var p=new URL(u).pathname;return p==="/"?new URL(u).host:p}
    catch(e){return u}}
  function tick(){$("elapsed").textContent=clock(base+(Date.now()-at)/1000)}
  function paint(s){
    base=s.elapsed||0;at=Date.now();tick();
    var pct=Math.max(3,Math.min(100,s.progress_percent||0));
    $("bar").style.width=pct+"%";$("barwrap").setAttribute("aria-valuenow",pct);
    $("pct").textContent=pct+"%";
    var at_=steps.indexOf(s.step);if(s.step==="done")at_=3;if(at_<0)at_=0;
    var times=s.times||{};
    steps.forEach(function(k,i){
      var li=$("step-"+k);li.className=i<at_?"done":(i===at_?"on":"");
      li.querySelector(".dot").textContent=i<at_?"\\u2713":(i===at_?"":String(i+1));
      li.querySelector(".took").textContent=times[k]!=null?fill(t.seconds,{n:Math.max(1,
        Math.round(times[k]))}):"";
    });
    var d=$("step-crawl").querySelector(".detail");
    if(at_===0&&s.pages_total){d.textContent=fill(t.read_of,{read:s.pages_read||0,
      total:s.pages_total});}
    else if(at_>0&&s.pages_total){d.textContent=fill(t.read_done,{n:s.pages_total})}
    $("now").textContent=at_===0&&s.current?fill(t.reading,{page:path(s.current)}):"";
    var pol=s.policies||[];$("policies").hidden=!pol.length;
    var ul=$("policy-list");ul.textContent="";
    pol.forEach(function(p){var li=document.createElement("li");li.textContent=p.label;
      ul.appendChild(li)});
    var prods=s.products||[];$("products").hidden=!prods.length;
    var grid=$("product-list");grid.textContent="";
    prods.forEach(function(p){
      var f=document.createElement("figure");
      if(/^https?:\\/\\//.test(p.image||"")){var img=document.createElement("img");
        img.src=p.image;img.alt="";img.loading="lazy";img.referrerPolicy="no-referrer";
        img.onerror=function(){img.replaceWith(Object.assign(document.createElement("div"),
          {className:"ph"}))};f.appendChild(img)}
      else{var ph=document.createElement("div");ph.className="ph";f.appendChild(ph)}
      var cap=document.createElement("figcaption");cap.textContent=p.name;
      var pr=document.createElement("div");pr.className="price";
      pr.textContent=money(p.price,p.currency);cap.appendChild(pr);f.appendChild(cap);
      grid.appendChild(f);
    });
    $("stale").hidden=!(s.stale&&s.can_retry);
  }
  function poll(){
    fetch(c.state,{cache:"no-store"}).then(function(r){return r.json()}).then(function(s){
      if(s.status!=="building"){location.reload();return}
      paint(s);setTimeout(poll,1500);
    }).catch(function(){setTimeout(poll,4000)});
  }
  setInterval(tick,1000);poll();
})();
"""


def _building_page(trial: service.Trial, token: str) -> HTMLResponse:
    t = T[trial.language]
    steps = "".join(
        f'<li id="step-{key}"{" class=on" if n == 0 else ""}><span class="dot" aria-hidden="true">'
        f'{"" if n == 0 else n + 1}</span><div><div class="what">{esc(t["step_" + key])}</div>'
        f'<div class="detail"></div></div><span class="took"></span></li>'
        for n, key in enumerate(STEPS)
    )
    keys = ("read_of", "read_done", "reading")
    config = {
        "lang": trial.language,
        "state": f"/probar/{token}/estado",
        "t": {**{k: t[k] for k in keys}, "seconds": "{n} s"},
    }
    data = json.dumps(config).replace("</", "<\\/")
    body = f"""<div class="build"><div class="card">
<span class="tag">{esc(t["trial_tag"])}</span>
<h1>{esc(t["building"])}</h1><p class="site">{esc(trial.domain)}</p>
<div class="bar" id="barwrap" role="progressbar" aria-label="{esc(t["building"])}"
 aria-valuemin="0" aria-valuemax="100" aria-valuenow="3"><span id="bar"></span></div>
<div class="meta"><span id="pct">3%</span>
<span>{esc(t["elapsed"])} <b id="elapsed">0:00</b></span></div>
<ol class="plan" aria-live="polite">{steps}</ol>
<p class="detail" id="now"></p>
<section id="policies" hidden><h2>{esc(t["found_policies"])}</h2>
<ul class="chips" id="policy-list"></ul></section>
<section id="products" hidden><h2>{esc(t["found_products"])}</h2>
<div class="found" id="product-list"></div></section>
<p class="calm">{esc(t["calm"])}</p>
<div class="notice" id="stale" role="status" hidden>{esc(t["stale"])}
<form method="post" action="/probar/{esc(token)}/reintentar" class="actions">
<button type="submit">{esc(t["retry"])}</button></form></div>
</div></div>
<script id="cfg" type="application/json">{data}</script>
<script>{BUILD_JS}</script>"""
    return _layout(t["building"], body, trial.language, extra_css=BUILD_CSS)


def _failed_page(conn: Any, trial: service.Trial, token: str) -> HTMLResponse:
    t = T[trial.language]
    retry = (
        f'<form method="post" action="/probar/{esc(token)}/reintentar" style="margin:0">'
        f'<button type="submit">{esc(t["retry"])}</button></form>'
        if service.can_retry(conn, trial)
        else ""
    )
    body = f"""<div class="build"><div class="card">
<span class="tag">{esc(t["trial_tag"])}</span>
<h1>{esc(t["failed"])}</h1><p class="site">{esc(trial.domain)}</p>
<p class="lead" role="alert" style="margin-top:14px">{esc(_failure(trial))}</p>
<div class="actions">{retry}
<form method="post" action="/probar/{esc(token)}/contacto" style="margin:0">
<button class="ghost" type="submit">{esc(t["talk"])}</button></form></div>
</div></div>"""
    return _layout(t["failed"], body, trial.language, extra_css=BUILD_CSS)


def _failure(trial: service.Trial) -> str:
    """El mensaje para el visitante según por qué falló (el detalle técnico queda guardado)."""
    t = T[trial.language]
    code = str(trial.progress.get("error_code") or "unreachable")
    return t.get(f"error_{code}", t["error_internal"]).format(domain=trial.domain)


def _percent(trial: service.Trial) -> int:
    if trial.status == "ready":
        return 100
    progress = trial.progress
    step = progress.get("step")
    if step == "agent":
        return 88
    if step == "index":
        return 70
    read = int(progress.get("pages_read") or 0)
    total = max(int(progress.get("pages_total") or 1), 1)
    return 5 + int(55 * min(read, total) / total)


def _date(value: object, language: str) -> str:
    """ "2026-11-22" como lo lee el visitante: "22 de noviembre" o "November 22"."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value or "")
    if language == "en":
        return f"{EN_MONTHS[day.month - 1]} {day.day}"
    return f"{day.day} de {ES_MONTHS[day.month - 1]}"


def _order_card(order: dict[str, Any], t: dict[str, str], language: str) -> str:
    statuses = json.loads(t["statuses"])
    item = (order.get("items") or [{}])[0]
    delay = order.get("delay") or {}
    extra = ""
    if delay:
        extra += (
            f'<div class="small"><b>{esc(t["delay"])}:</b> {esc(delay.get("reason"))} · '
            f"{esc(_date(delay.get('new_estimated_delivery'), language))}</div>"
        )
    if order.get("tracking_number"):
        extra += (
            f'<div class="small muted">{esc(t["tracking"])}: {esc(order.get("carrier"))} '
            f"{esc(order['tracking_number'])}</div>"
        )
    return (
        f'<div class="order"><div class="row"><b>{esc(order["id"])}</b>'
        f'<span class="small">{esc(statuses.get(order["status"], order["status"]))}</span></div>'
        f'<div class="small">{esc(item.get("name"))} · '
        f"{esc(i18n.money(order.get('total'), language))}</div>{extra}</div>"
    )


def _ready_page(view: service.View, token: str, notice: str) -> HTMLResponse:
    trial = view.trial
    t = T[trial.language]
    orders = "".join(_order_card(o, t, trial.language) for o in view.orders)
    products = "".join(
        "<figure>"
        + (f'<img src="{esc(p["image"])}" alt="">' if p.get("image") else "")
        + f"<figcaption>{esc(p['name'])}</figcaption></figure>"
        for p in trial.products
    )
    generic = (
        f'<p class="small muted">{esc(t["generic"])}</p>' if trial.progress.get("generic") else ""
    )
    left = max(service.MAX_MESSAGES - trial.messages, 0)
    talked = (
        f'<p class="small" role="status">{esc(t["talked"].format(email=trial.email))}</p>'
        if notice == "contacto" or trial.contact_requested_at
        else ""
    )
    tries = "".join(
        f'<li><button type="button" data-copy="{esc(s)}">{esc(s)}</button></li>'
        for s in _suggestions(trial)
    )
    body = f"""<div class="top"><div><span class="tag">{esc(t["trial_tag"])}</span>
<h1>{esc(trial.name)}</h1><p class="lead">{esc(t["ready_lead"])}</p></div>
<div class="actions"><a class="button" href="/probar/{esc(token)}/piloto">{esc(t["pilot"])}</a>
<form method="post" action="/probar/{esc(token)}/contacto" style="margin:0">
<button class="ghost" type="submit">{esc(t["talk"])}</button></form></div></div>{talked}
<div class="grid"><div>
<iframe src="/demo/{esc(view.chat_slug)}" title="{esc(trial.name)}"></iframe>
<p class="small muted">{esc(t["left"].format(left=left, total=service.MAX_MESSAGES))}</p></div>
<aside>
<div class="card"><h2>{esc(t["orders"])} <span class="tag">demo</span></h2>
<p class="small muted">{esc(t["orders_note"].format(email=trial.email))}</p>{orders}</div>
<div class="card"><h2>{esc(t["tries"])}</h2><ul class="tries">{tries}</ul></div>
<div class="card"><h2>{esc(t["approvals"])}</h2>
<p class="small muted">{esc(t["approvals_note"])}</p><div id="approvals"
 class="small muted">{esc(t["none"])}</div></div>
<div class="card"><h2>{esc(t["products"])}</h2>{generic}<div class="products">{products}</div></div>
</aside></div>
<script>
(function(){{
  var base="/probar/{esc(token)}", none={json.dumps(t["none"])};
  var labels={{approve:{json.dumps(t["approve"])},reject:{json.dumps(t["reject"])}}};
  document.querySelectorAll("[data-copy]").forEach(function(b){{
    b.addEventListener("click",function(){{
      if(navigator.clipboard)navigator.clipboard.writeText(b.dataset.copy);
    }});
  }});
  function render(list){{
    var box=document.getElementById("approvals");
    if(!list.length){{box.textContent=none;return}}
    box.textContent="";
    list.forEach(function(a){{
      var row=document.createElement("div");row.className="approval";
      var text=document.createElement("span");text.textContent=a.label;row.appendChild(text);
      var acts=document.createElement("div");acts.className="actions";
      [["aprobar",labels.approve,""],["rechazar",labels.reject,"ghost"]].forEach(function(d){{
        var b=document.createElement("button");b.type="button";b.textContent=d[1];
        b.className=d[2];
        b.addEventListener("click",function(){{
          b.disabled=true;
          fetch(base+"/aprobaciones/"+a.id,{{method:"POST",
            headers:{{"Content-Type":"application/x-www-form-urlencoded"}},
            body:"decision="+d[0]}}).then(load);
        }});
        acts.appendChild(b);
      }});
      row.appendChild(acts);box.appendChild(row);
    }});
  }}
  function load(){{
    fetch(base+"/aprobaciones",{{cache:"no-store"}}).then(function(r){{return r.json()}})
    .then(function(d){{render(d.approvals||[])}}).catch(function(){{}});
  }}
  load();setInterval(load,3000);
}})();
</script>"""
    return _layout(trial.name, body, trial.language)


def _suggestions(trial: service.Trial) -> list[str]:
    first = trial.products[0]["name"] if trial.products else ""
    return [s.format(product=first) for s in service.TEXTS[trial.language]["suggestions"]]


def _message_page(
    language: str, title: str, lead: str, link: str = "", status: int = 200
) -> HTMLResponse:
    t = T[language]
    action = f'<a class="button" href="{esc(link)}">{esc(t["login"])}</a>' if link else ""
    body = f"""<div class="narrow"><div class="card"><h1>{esc(title)}</h1>
<p class="lead">{esc(lead)}</p>{action}</div></div>"""
    return _layout(title, body, language, status)


def _not_found() -> HTMLResponse:
    return _message_page(
        "es", "La prueba no existe", "Revisá el enlace o pedí una prueba nueva.", status=404
    )


@router.get("/probar/{token}", response_class=HTMLResponse)
def trial_page(token: str, request: Request, deps: Deps) -> HTMLResponse:
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None:
            return _not_found()
        t = T[trial.language]
        if trial.status == "code_sent":
            return _code_page(trial, token)
        if trial.status == "building":
            return _building_page(trial, token)
        if trial.status == "failed":
            return _failed_page(conn, trial, token)
        if trial.status == "converted":
            return _message_page(
                trial.language, t["converted"], t["converted_lead"], "/panel/login"
            )
        if trial.status == "expired":
            return _message_page(trial.language, t["expired"], "", status=410)
        view = service.view(conn, trial)
    return _ready_page(view, token, request.query_params.get("aviso", ""))


@router.post("/probar/{token}/codigo")
def trial_code(token: str, form: Form, deps: Deps) -> Response:
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None:
            return _not_found()
        try:
            service.confirm(conn, token, form.get("codigo", ""))
        except service.TrialError as error:
            return _code_page(trial, token, str(error))
    deps.kick_worker()  # el armado corre en el worker del onboarding
    return RedirectResponse(f"/probar/{token}", status_code=303)


@router.get("/probar/{token}/estado")
def trial_state(token: str, deps: Deps) -> JSONResponse:
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        view = service.view(conn, trial)
        seconds = service.elapsed(conn, trial.id)
        retry = service.can_retry(conn, trial)
    progress = trial.progress
    t = T[trial.language]
    building = trial.status == "building"
    data = {
        "status": trial.status,
        "step": progress.get("step"),
        "pages": progress.get("pages", 0),
        "name": trial.name,
        "error": progress.get("error"),
        "generic": bool(progress.get("generic")),
        # Mientras se arma, los productos que va encontrando (nombre, precio y foto).
        "products": trial.products if trial.status == "ready" else progress.get("found") or [],
        "orders": view.orders,
        "chat_slug": view.chat_slug,
        "messages_left": max(service.MAX_MESSAGES - trial.messages, 0),
        # El avance en vivo (decisión 099).
        "pages_read": progress.get("pages_read", 0),
        "pages_total": progress.get("pages_total", 0),
        "current": progress.get("current") or "",
        "policies": [
            {"kind": k, "label": t[f"policy_{k}"]}
            for k in progress.get("policies") or []
            if f"policy_{k}" in t
        ],
        "times": progress.get("times") or {},
        "progress_percent": _percent(trial),
        "elapsed": round(seconds, 1),
        "stale": building and seconds > service.STALE_SECONDS,
        "can_retry": retry,
        "text_only": bool(progress.get("text_only")),
        "message": _failure(trial) if trial.status == "failed" else "",
    }
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/probar/{token}/reintentar")
def trial_retry(token: str, deps: Deps) -> Response:
    """ "Reintentar" (si falló o lleva más de 2 minutos). Un doble clic arma una sola vez."""
    with deps.connection() as conn:
        if service.find(conn, token) is None:
            return _not_found()
        retried = service.retry(conn, token)
    if retried:
        deps.kick_worker()
    return RedirectResponse(f"/probar/{token}", status_code=303)


@router.get("/probar/{token}/aprobaciones")
def trial_approvals(token: str, deps: Deps) -> JSONResponse:
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None or trial.status != "ready" or trial.tenant_id is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        found = service.pending_approvals(conn, trial.tenant_id)
    return JSONResponse({"approvals": found}, headers={"Cache-Control": "no-store"})


@router.post("/probar/{token}/aprobaciones/{approval_id}")
def trial_decide(token: str, approval_id: uuid.UUID, form: Form, deps: Deps) -> JSONResponse:
    """El mini panel: el visitante aprueba o rechaza, con el mismo `decide` del panel (se ejecuta
    por el guardián) y el mismo aviso al cliente en el chat."""
    approve = form.get("decision") == "aprobar"
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None or trial.status != "ready" or trial.tenant_id is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        tenant_id = trial.tenant_id
        with tenant_scope(conn, tenant_id):
            row = conn.execute(
                "select a.case_id, t.name, a.params from approvals a"
                " join tools t on t.id = a.tool_id where a.id = %s",
                (approval_id,),
            ).fetchone()
        if row is None:  # no existe o es de otra empresa: la misma respuesta
            return JSONResponse({"error": "not_found"}, status_code=404)
        case_id, tool, params = row
        try:
            outcome = decide(
                conn, deps.guardian(conn, tenant_id), tenant_id, approval_id, approve=approve
            )
        except ApprovalError:
            return JSONResponse({"error": "already_decided"}, status_code=409)
        language = i18n.tenant_language(conn, tenant_id)
        notify_customer(
            conn,
            tenant_id,
            case_id,
            i18n.approval_message(language, tool, params, outcome.status),
            kind="approval_decided",
            detail={
                "approval_id": str(approval_id),
                "decision": "aprobar" if approve else "rechazar",
                "status": outcome.status,
                "decided_by": "prueba-gratis",
            },
        )
    return JSONResponse({"status": outcome.status})


@router.post("/probar/{token}/contacto")
def trial_contact(token: str, deps: Deps) -> Response:
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None:
            return _not_found()
        service.contact_operator(conn, deps.mailer(), trial)
    return RedirectResponse(f"/probar/{token}?aviso=contacto", status_code=303)


@router.get("/probar/{token}/piloto")
def trial_pilot(token: str, deps: Deps) -> Response:
    with deps.connection() as conn:
        trial = service.find(conn, token)
        if trial is None:
            return _not_found()
        service.request_pilot(conn, trial.id)
    return RedirectResponse(f"/registro?prueba={token}", status_code=303)
