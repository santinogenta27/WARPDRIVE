"""HTML del panel: sistema de diseño, layout y ayudas (decisiones 022 y 085). Páginas servidas por
la API, sin recursos externos. Todo texto que viene de la base o del cliente pasa por `esc`.

Layout (decisión 085): barra lateral con las 9 secciones del producto y, solo para el operador de
la plataforma, una zona aparte. Cada sección puede tener pestañas (`SECTIONS`). En el celular la
barra lateral se abre con el botón del menú.
"""

import html
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.panel import english, spanish

if TYPE_CHECKING:
    from app.panel.auth import Session

PRODUCT = "Enterprise Data AI"
TAGLINE = "AI Support & Sales Agent"

# -- sistema de diseño ------------------------------------------------------------------------
# Un solo juego de tokens. Tema claro por defecto; el oscuro se elige en el menú del usuario
# (`data-theme="dark"`).
CSS = """
:root{--bg:#f7f7f8;--surface:#fff;--sunken:#f1f2f4;--ink:#16181d;--ink-2:#3c414b;--muted:#646b77;
--line:#e4e6ea;--line-strong:#d3d6dc;--brand:#4338ca;--brand-ink:#3730a3;--brand-soft:#eef0fd;
--on-brand:#fff;--ok:#157f3c;--ok-soft:#e5f6ea;--warn:#a0520b;--warn-soft:#fdf1df;
--bad:#c02626;--bad-soft:#fdecec;--focus:#6366f1;--shadow:0 1px 2px rgba(16,24,40,.06);
--shadow-lg:0 12px 32px -12px rgba(16,24,40,.28);--radius:10px;--side:248px;color-scheme:light}
:root[data-theme=dark]{--bg:#0e1014;--surface:#161920;--sunken:#1c2029;--ink:#e8eaee;
--ink-2:#c3c8d1;--muted:#9299a6;--line:#262a33;--line-strong:#343944;--brand:#8b93f8;
--brand-ink:#a5acfa;--brand-soft:#20233d;--on-brand:#0e1014;--ok:#4ade80;--ok-soft:#10291b;
--warn:#fbbf24;--warn-soft:#2e230c;--bad:#f87171;--bad-soft:#341617;--focus:#8b93f8;
--shadow:none;--shadow-lg:0 12px 32px -12px rgba(0,0,0,.7);color-scheme:dark}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
font:14.5px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
a{color:var(--brand-ink)}a:hover{text-decoration-thickness:2px}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:6px}
.skip{position:absolute;left:-999px;top:8px;z-index:30;background:var(--surface);padding:8px 12px}
.skip:focus{left:8px}
/* layout */
.shell{display:flex;min-height:100vh}
@media (min-width:901px){.shell{background:linear-gradient(to right,var(--surface) var(--side),
var(--line) var(--side),var(--line) calc(var(--side) + 1px),var(--bg) calc(var(--side) + 1px))}}
.side{position:sticky;top:0;height:100vh;width:var(--side);flex:none;display:flex;
flex-direction:column;gap:6px;padding:14px 12px;background:var(--surface);
border-right:1px solid var(--line);overflow-y:auto}
.product{display:flex;align-items:center;gap:10px;padding:4px 6px 10px}
.logo{width:32px;height:32px;border-radius:9px;background:var(--brand);color:var(--on-brand);
display:grid;place-items:center;flex:none}.logo svg{width:18px;height:18px}
.product b{display:block;font-size:14px;letter-spacing:-.01em}
.product small{display:block;color:var(--muted);font-size:12px;line-height:1.3}
.workspace{display:flex;align-items:center;gap:8px;margin:0 2px 8px;padding:7px 9px;
border:1px solid var(--line);border-radius:9px;font-weight:600;font-size:13.5px;min-width:0}
.workspace .ws{width:22px;height:22px;border-radius:6px;background:var(--sunken);flex:none;
display:grid;place-items:center;font-size:12px;color:var(--ink-2)}
.workspace span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.menu{display:flex;flex-direction:column;gap:1px}
.menu a{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;
color:var(--ink-2);text-decoration:none;font-weight:500}
.menu a:hover{background:var(--sunken);color:var(--ink)}
.menu a.on{background:var(--brand-soft);color:var(--brand-ink);font-weight:600}
.menu svg,.icon{width:17px;height:17px;flex:none}
.menu .count{margin-left:auto;min-width:20px;height:20px;padding:0 6px;border-radius:10px;
background:var(--warn);color:var(--surface);font-size:11.5px;font-weight:700;line-height:20px;
text-align:center}
.group{margin:14px 9px 4px;font-size:11.5px;font-weight:600;color:var(--muted);
text-transform:uppercase;letter-spacing:.05em}
.side-foot{margin-top:auto;padding-top:10px;border-top:1px solid var(--line);display:flex;
flex-direction:column;gap:6px}
.who{font-size:12.5px;color:var(--muted);padding:0 9px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.prefs{display:flex;gap:6px;padding:0 4px;flex-wrap:wrap}.prefs form{margin:0}
.prefs button{padding:5px 9px;font-size:12.5px}
.content{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{display:none}
main{width:100%;max-width:1120px;margin:0 auto;padding:28px 32px 64px}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin:-6px 0 22px;
overflow-x:auto;scrollbar-width:none}
.tabs a{padding:9px 12px;color:var(--muted);text-decoration:none;font-weight:500;
border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.tabs a:hover{color:var(--ink)}
.tabs a.on{color:var(--ink);border-color:var(--brand);font-weight:600}
.eyebrow{font-size:12.5px;font-weight:600;color:var(--muted);margin:0 0 4px}
@media (max-width:900px){
.side{position:fixed;z-index:20;left:0;top:0;transform:translateX(-105%);
transition:transform .18s ease;box-shadow:var(--shadow-lg)}
body.menu-open .side{transform:none}
body.menu-open .scrim{display:block}
.scrim{display:none;position:fixed;inset:0;z-index:15;background:rgba(10,12,16,.4)}
.topbar{display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:10;
padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--line)}
.topbar b{font-size:15px}.topbar button{padding:6px 8px;line-height:0}
main{padding:18px 16px 48px}}
@media (prefers-reduced-motion:reduce){.side{transition:none}}
/* tipografía */
h1{font-size:22px;line-height:1.25;letter-spacing:-.015em;margin:0 0 4px}
h2{font-size:16px;letter-spacing:-.01em;margin:28px 0 10px}h3{font-size:14.5px;margin:16px 0 6px}
.lead{color:var(--muted);margin:0 0 20px;max-width:72ch}
.muted{color:var(--muted)}.small{font-size:13px}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px}
.ellipsis{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.help{color:var(--muted);font-size:13px;margin:4px 0 0}
.example{color:var(--muted);font-size:12.5px;margin:2px 0 0}
/* superficies */
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px;
margin-bottom:12px;box-shadow:var(--shadow)}
.card>h2:first-child,.card>h3:first-child{margin-top:0}
.card.attention{border-color:var(--warn)}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:0}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.pagehead{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.pagehead>div:first-child{flex:1;min-width:0}
/* badges de estado */
.badge{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;
font-size:12px;font-weight:600;background:var(--sunken);color:var(--ink-2);white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;
opacity:.8}
.badge.escalated,.badge.fallo,.badge.bad,.badge.failed{background:var(--bad-soft);color:var(--bad)}
.badge.waiting_approval,.badge.en_curso,.badge.warn,.badge.processing,.badge.setup,
.badge.paused{background:var(--warn-soft);color:var(--warn)}
.badge.resolved,.badge.hecha,.badge.ok,.badge.ready,.badge.live{background:var(--ok-soft);
color:var(--ok)}
.badge.open,.badge.info{background:var(--brand-soft);color:var(--brand-ink)}
/* botones y campos */
button,.btn,.button{display:inline-flex;align-items:center;justify-content:center;gap:6px;
border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);
border-radius:8px;padding:7px 13px;font:inherit;font-weight:550;cursor:pointer;
text-decoration:none;line-height:1.3;box-shadow:var(--shadow)}
button:hover,.btn:hover,.button:hover{background:var(--sunken)}
button:disabled{opacity:.5;cursor:not-allowed}
.btn.primary,button.primary,.button.primary{background:var(--brand);border-color:var(--brand);
color:var(--on-brand)}
.btn.primary:hover,button.primary:hover{background:var(--brand-ink);border-color:var(--brand-ink)}
button.danger,.btn.danger{color:var(--bad)}button.danger.solid{background:var(--bad);
border-color:var(--bad);color:#fff}
button.link{border:0;background:none;box-shadow:none;padding:0;color:var(--brand-ink);
font-weight:500}
input,textarea,select{width:100%;padding:8px 11px;border:1px solid var(--line-strong);
border-radius:8px;background:var(--surface);color:var(--ink);font:inherit;font-size:16px}
@media (min-width:901px){input,textarea,select{font-size:14.5px}}
textarea{min-height:90px;resize:vertical}
label{display:block;margin:14px 0 4px;font-weight:600}
input[type=checkbox],input[type=radio]{width:auto;accent-color:var(--brand)}
input[type=file]{padding:6px}
/* tablas */
.table{overflow-x:auto}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);font-size:13.5px;
vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px}tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums}
/* KPIs */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;
margin-bottom:18px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;
box-shadow:var(--shadow)}
.kpi .n{font-size:26px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi.big .n{color:var(--brand-ink)}
.track{height:8px;border-radius:99px;background:var(--sunken);overflow:hidden}
.fill{height:100%;border-radius:99px;background:var(--brand)}.fill.ok{background:var(--ok)}
.fill.warn{background:var(--warn)}.fill.bad{background:var(--bad)}
.type{display:grid;grid-template-columns:minmax(150px,1.3fr) 1fr 1fr;gap:14px;align-items:center}
@media (max-width:640px){.type{grid-template-columns:1fr}.msg{max-width:95%}}
/* conversación */
.msgs{display:flex;flex-direction:column;gap:8px}.msg{max-width:80%;padding:9px 12px;
border-radius:14px;background:var(--sunken)}.msg.customer{align-self:flex-start}
.msg.agent{align-self:flex-end;background:var(--brand-soft)}
.msg.human{align-self:flex-end;background:var(--ok-soft)}.msg .who{font-size:11px;
color:var(--muted);margin-bottom:2px;padding:0}
ol.audit{list-style:none;padding:0;margin:0}
ol.audit li{border-left:3px solid var(--line);padding:6px 0 10px 12px;margin-left:4px}
ol.audit li b{display:block}
/* pasos */
.steps{list-style:none;padding:0;margin:0}
.steps li{display:flex;gap:12px;align-items:baseline;padding:10px 0;
border-bottom:1px solid var(--line)}.steps li:last-child{border:0}
.missing{margin:8px 0 0;padding-left:18px;color:var(--warn);font-size:13px}
/* avisos */
.alert{border-color:var(--bad);background:var(--bad-soft);color:var(--ink)}
.okmsg{border-color:var(--ok);background:var(--ok-soft)}
.note{border-left:3px solid var(--brand);background:var(--brand-soft);padding:10px 14px;
border-radius:8px;margin:10px 0}
pre{overflow:auto;background:var(--sunken);padding:10px;border-radius:8px;font-size:12.5px}
details>summary{cursor:pointer}
.empty{text-align:center;color:var(--muted);padding:32px 20px}
.empty b{display:block;color:var(--ink);font-size:15px;margin-bottom:4px}
.empty .btn,.empty .button{margin-top:12px}
form.sending button{opacity:.6;cursor:wait}
.toast{position:fixed;top:16px;left:50%;z-index:40;transform:translateX(-50%);
max-width:calc(100vw - 32px);padding:10px 16px;border-radius:10px;background:var(--ink);
color:var(--surface);font-weight:600;box-shadow:var(--shadow-lg);
animation:toast 5s ease forwards}
@keyframes toast{0%{opacity:0;transform:translate(-50%,-8px)}6%,84%{opacity:1;
transform:translate(-50%,0)}100%{opacity:0;transform:translate(-50%,0);visibility:hidden}}
@media (prefers-reduced-motion:reduce){.toast{animation:none}}
.auth{max-width:420px;margin:56px auto;padding:0 16px}
.auth .product{justify-content:center;margin-bottom:18px}
footer{text-align:center;color:var(--muted);font-size:12px;padding:12px}
"""

# -- iconos (trazos propios, 24×24) -----------------------------------------------------------
ICONS = {
    "home": "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    "inbox": "M3 13h5l2 3h4l2-3h5M5.5 5h13L21 13v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6z",
    "customers": "M16 20v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8m13 9"
    "v-1a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
    "agent": "M12 3v3M8 8h8a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-5a3 3 0 0 1 3-3zm1.5 5"
    "v1m5-1v1",
    "knowledge": "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zm0 0A2.5 2.5 0 0 0 6.5"
    " 22H20v-5M8 7h8M8 11h6",
    "deploy": "M22 2 11 13M22 2l-7 20-4-9-9-4z",
    "test": "M9 3h6m-5 0v6l-5.5 10a1 1 0 0 0 .9 1.5h13.2a1 1 0 0 0 .9-1.5L14 9V3M7 15h10",
    "insights": "M3 3v18h18M7 15l4-4 3 3 5-6",
    "settings": "M4 21v-7m0-4V3m8 18v-9m0-4V3m8 18v-5m0-4V3M1 14h6m2-6h6m2 8h6",
    "companies": "M3 21h18M5 21V7l7-4 7 4v14M9 9h1m4 0h1m-6 4h1m4 0h1m-6 4h1m4 0h1",
    "trials": "M12 8v4l3 2m-3 7a9 9 0 1 0 0-18 9 9 0 0 0 0 18",
    "billing": "M3 6h18v13H3zm0 4h18M7 15h3",
    "behind": "M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12zm11 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6",
    "spark": "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
    "menu": "M3 6h18M3 12h18M3 18h18",
    "out": "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 14 5-5-5-5m5 5H9",
}


def icon(name: str, cls: str = "icon") -> str:
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f'<path d="{ICONS[name]}"/></svg>'
    )


# -- navegación (decisión 085) ------------------------------------------------------------------
# (clave, etiqueta, dirección, pestañas). La clave de una pestaña es "sección/subsección"; la
# primera pestaña de cada sección usa la clave de la sección.
Tab = tuple[str, str, str]
SECTIONS: list[tuple[str, str, str, list[Tab]]] = [
    ("home", "Home", "/panel/home", []),
    (
        "inbox",
        "Inbox",
        "/panel/inbox",
        [
            ("inbox", "Conversations", "/panel/inbox"),
            ("inbox/approvals", "Approvals", "/panel/inbox/approvals"),
            ("inbox/drafts", "Drafts", "/panel/inbox/drafts"),
        ],
    ),
    ("customers", "Customers", "/panel/customers", []),
    (
        "agent",
        "Agent",
        "/panel/agent",
        [
            ("agent", "Rules", "/panel/agent"),
            ("agent/actions", "Actions", "/panel/agent/actions"),
            ("agent/topics", "Topics", "/panel/agent/topics"),
            ("agent/voice", "Brand & voice", "/panel/agent/voice"),
            ("agent/sales", "Sales", "/panel/agent/sales"),
            ("agent/language", "Language", "/panel/agent/language"),
        ],
    ),
    ("knowledge", "Knowledge", "/panel/knowledge", []),
    ("deploy", "Deploy", "/panel/deploy", []),
    (
        "test",
        "Test & Improve",
        "/panel/test",
        [
            ("test", "Readiness", "/panel/test"),
            ("test/playground", "Playground", "/panel/test/playground"),
            ("test/simulations", "Simulations", "/panel/test/simulations"),
            ("test/improve", "To improve", "/panel/test/improve"),
        ],
    ),
    (
        "insights",
        "Insights",
        "/panel/insights",
        [
            ("insights", "Overview", "/panel/insights"),
            ("insights/results", "Results", "/panel/insights/results"),
        ],
    ),
    (
        "settings",
        "Settings",
        "/panel/settings",
        [
            ("settings", "Team", "/panel/settings"),
            ("settings/usage", "Usage & model", "/panel/settings/usage"),
            ("settings/data", "Your data", "/panel/settings/data"),
        ],
    ),
]
# Zona del operador de la plataforma: un cliente nunca la ve (ni la puede abrir).
OPERATOR: list[tuple[str, str, str, str]] = [
    ("operator/companies", "Companies", "/panel/operator/companies", "companies"),
    ("operator/trials", "Trials", "/panel/operator/trials", "trials"),
    ("operator/billing", "Billing", "/panel/operator/billing", "billing"),
    ("operator/behind", "Behind the scenes", "/panel/operator/behind", "behind"),
]
# Lo que una persona del equipo (rol `agent`) no ve ni puede abrir (decisión 058). Incluye los
# segmentos de las direcciones de los formularios de antes, que siguen sirviendo.
ADMIN_ONLY = {"knowledge", "agent", "deploy", "settings", "onboarding", "configuracion", "equipo"}
# Aprobaciones pendientes de la empresa de la sesión: `enter` lo fija en cada pedido.
PENDING: ContextVar[int] = ContextVar("panel_pending", default=0)
# ¿Es una empresa de demo nuestra (Acme, Northwind)? Su usuario ve "Behind the scenes".
DEMO: ContextVar[bool] = ContextVar("panel_demo", default=False)

# Confirmación breve después de decidir una aprobación (`?aviso=…`). El texto sale de esta lista:
# lo que venga en la URL no se muestra.
NOTICES = {
    "reembolso-email": "Reembolso aprobado · se envió el email de confirmación",
    "reembolso-sin-email": "Reembolso aprobado · no se pudo enviar el email de confirmación",
    "reembolso": "Reembolso aprobado",
    "aprobado": "Solicitud aprobada",
    "fallo": "Aprobada, pero el sistema de la empresa no pudo completarla",
    "rechazado": "Solicitud rechazada",
    "revisada": "Revisión guardada",
    "eval-creado": "Caso de prueba creado · lo prueba el reporte de preparación",
    "borrador-enviado": "Respuesta enviada al cliente",
    "borrador-rechazado": "Borrador rechazado · no se envió nada",
    # Memoria de los clientes (decisión 078).
    "memoria-guardada": "Memoria del cliente corregida",
    "memoria-borrada": "Memoria del cliente borrada",
    "retencion-guardada": "Retención guardada",
    "retencion-invalida": "La retención va de 1 a 36 meses",
}


def toast(key: str | None) -> str:
    text = NOTICES.get(key or "")
    if text is None:
        return ""
    # Se va sola; sin `?aviso` en la dirección, recargar no la vuelve a mostrar.
    return (
        f'<div class="toast" role="status"><span aria-hidden="true">✓</span> {esc(text)}</div>'
        "<script>setTimeout(function(){var t=document.querySelector('.toast');"
        "if(t)t.remove()},5200);history.replaceState(null,'',location.pathname)</script>"
    )


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


# Hora de Buenos Aires: Argentina no tiene horario de verano. Con un desplazamiento fijo no hace
# falta la base de zonas horarias (en Windows `zoneinfo` necesita el paquete tzdata).
ARGENTINA = timezone(timedelta(hours=-3))


def local_time(value: datetime | str, fmt: str = "%d/%m %H:%M") -> str:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    shown = moment.astimezone(ARGENTINA).strftime(fmt)
    return english.english_date(shown, fmt) if english.english() else shown


def _tabs(active: str) -> str:
    section = active.split("/", 1)[0]
    for key, _, _, tabs in SECTIONS:
        if key == section and tabs:
            links = "".join(
                f'<a href="{url}" class="{"on" if tab == active else ""}"'
                f"{' aria-current=page' if tab == active else ''}>{esc(label)}</a>"
                for tab, label, url in tabs
            )
            return f'<nav class="tabs" aria-label="Section">{links}</nav>'
    return ""


def _sidebar(session: "Session", active: str) -> str:
    from app.panel import auth

    section = active.split("/", 1)[0]
    waiting = PENDING.get()
    count = (
        f'<span class="count" aria-label="{waiting} waiting for approval">{waiting}</span>'
        if waiting
        else ""
    )
    links = "".join(
        f'<a href="{url}" class="{"on" if key == section else ""}"'
        f"{' aria-current=page' if key == section else ''}>{icon(key)}<span>{esc(label)}</span>"
        f"{count if key == 'inbox' else ''}</a>"
        for key, label, url, _ in SECTIONS
        if not (session.role == "agent" and key in ADMIN_ONLY)
    )
    operator = ""
    if session.admin or DEMO.get():
        items = "".join(
            f'<a href="{url}" class="{"on" if key == active else ""}">{icon(name)}'
            f"<span>{esc(label)}</span></a>"
            for key, label, url, name in OPERATOR
            if session.admin or key == "operator/behind"
        )
        operator = (
            f'<div class="group">Operator</div>'
            f'<nav class="menu" aria-label="Operator">{items}</nav>'
        )
    csrf = auth.csrf_token(session.token)
    other = ("es", "Español") if english.english() else ("en", "English")
    dark = english.THEME.get() == "dark"
    company = session.company or PRODUCT
    return f"""<aside class="side" id="side" aria-label="Main">
<div class="product" translate="no"><div class="logo">{icon("spark")}</div><div><b>{PRODUCT}</b>
<small>{esc(TAGLINE)}</small></div></div>
<div class="workspace" title="Company"><span class="ws" aria-hidden="true">{
        esc(company[:1].upper())
    }</span><span>{esc(company)}</span></div>
<nav class="menu" aria-label="Sections">{links}</nav>{operator}
<div class="side-foot"><div class="who">{esc(session.email)}</div>
<div class="prefs">
<form method="post" action="/panel/preferences"><input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="lang" value="{other[0]}">
<button type="submit" lang="{other[0]}" translate="no">{other[1]}</button></form>
<form method="post" action="/panel/preferences"><input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="theme" value="{"light" if dark else "dark"}">
<button type="submit">{"Light theme" if dark else "Dark theme"}</button></form>
<form method="post" action="/panel/logout"><button type="submit">{icon("out")}Sign out</button>
</form></div></div></aside>"""


MENU_JS = (
    "(function(){var b=document.getElementById('menu-btn'),s=document.getElementById('scrim');"
    "function t(o){document.body.classList.toggle('menu-open',o);"
    "if(b)b.setAttribute('aria-expanded',o?'true':'false')}"
    "if(b)b.addEventListener('click',function(){t(!document.body.classList.contains('menu-open'))});"
    "if(s)s.addEventListener('click',function(){t(false)});"
    "document.addEventListener('keydown',function(e){if(e.key==='Escape')t(false)})})();"
)


def page(
    title: str,
    body: str,
    *,
    active: str = "",
    logged_in: bool = True,
    session: "Session | None" = None,
    refresh: int | None = None,
) -> str:
    """`session`: de ahí salen la empresa y el menú. `active`: la sección o "sección/pestaña".
    `refresh`: segundos para volver a cargar la página sola (avance del análisis)."""
    lang = "en" if english.english() else "es"
    reload = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    theme = ' data-theme="dark"' if english.THEME.get() == "dark" else ""
    # El título va junto al nombre del producto: se traduce solo.
    title = english.translate_text(title) if lang == "en" else spanish.translate_text(title)
    head = f"""<!doctype html>
<html lang="{lang}"{theme}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{reload}
<title>{esc(title)} · {PRODUCT}</title><style>{CSS}</style></head>"""
    if logged_in and session is not None:
        name = next((label for key, label, _, _ in SECTIONS if key == active.split("/")[0]), title)
        markup = f"""{head}<body><a class="skip" href="#main">Skip to content</a>
<div class="shell">{_sidebar(session, active)}<div class="scrim" id="scrim"></div>
<div class="content"><header class="topbar"><button type="button" id="menu-btn"
 aria-controls="side" aria-expanded="false" aria-label="Open menu">{icon("menu")}</button>
<b>{esc(name)}</b></header>
<main id="main">{_tabs(active)}{body}</main></div></div>
<script>{NO_DOUBLE_SUBMIT}{MENU_JS}</script></body></html>"""
    else:
        markup = f"""{head}<body><main id="main" class="auth-main">{body}</main>
<script>{NO_DOUBLE_SUBMIT}</script></body></html>"""
    return translate(markup)


def translate(markup: str) -> str:
    """Las páginas mezclan texto en inglés (lo nuevo) y en español (lo de antes): cada idioma
    traduce el otro con su catálogo (decisión 085)."""
    if english.english():
        return english.translate_html(markup)
    return english.translate_html(markup, spanish.translate_text)


def auth_card(body: str) -> str:
    """Tarjeta centrada de las pantallas sin sesión (login, registro, invitación)."""
    return f"""<div class="auth"><div class="product" translate="no">
<div class="logo">{icon("spark")}</div>
<div><b>{PRODUCT}</b><small>{esc(TAGLINE)}</small></div></div>
<div class="card">{body}</div></div>"""


# Doble clic en Enviar o Aprobar: el segundo envío del mismo formulario no sale (el servidor
# además descarta un envío repetido).
NO_DOUBLE_SUBMIT = (
    'document.addEventListener("submit",function(e){var f=e.target;'
    'if(f.dataset.sent){e.preventDefault();return}f.dataset.sent="1";f.classList.add("sending")});'
)


STATUS = {
    "escalated": "Derivado",
    "waiting_approval": "Esperando aprobación",
    "open": "Abierto",
    "resolved": "Resuelto",
}


RESOLVED_BY = {"agent": "por el agente", "human": "con una persona"}
STATUS_EN = {
    "escalated": "Handed off",
    "waiting_approval": "Waiting for approval",
    "open": "Open",
    "resolved": "Resolved",
}
RESOLVED_BY_EN = {"agent": "by the agent", "human": "with a person"}


def status_badge(status: str, resolved_by: str | None = None, reopened: bool = False) -> str:
    """Estado del caso. Un resuelto dice quién lo resolvió (la unidad de cobro es el caso
    resuelto por el agente)."""
    en = english.english()
    statuses, who = (STATUS_EN, RESOLVED_BY_EN) if en else (STATUS, RESOLVED_BY)
    label = statuses.get(status, status)
    if status == "resolved" and resolved_by in who:
        label = f"{label} {who[resolved_by]}"
    if reopened:  # el cliente volvió por el mismo tema: ya no cuenta para el cobro
        label = f"{label} · {'reopened' if en else 'reabierto'}"
    return f'<span class="badge {esc(status)}">{esc(label)}</span>'
