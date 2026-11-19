"""HTML del panel: layout común y ayudas. Páginas servidas por la API, sin recursos externos
(decisión 022). Todo texto que viene de la base o del cliente pasa por `esc`."""

import html
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.panel import english

if TYPE_CHECKING:
    from app.panel.auth import Session

CSS = """
:root{--bg:#f3f4f7;--surface:#fff;--ink:#14171c;--muted:#667085;--line:#e4e7ec;
--brand:#1e3a5f;--brand-soft:#e8eef6;--ok:#15803d;--ok-soft:#dcfce7;--warn:#b45309;
--warn-soft:#fef3c7;--bad:#b91c1c;--bad-soft:#fee2e2;--on-brand:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0b0d11;--surface:#14171c;--ink:#e7e9ed;
--muted:#9aa1ab;--line:#262a31;--brand:#5b8cc8;--brand-soft:#1b2633;--ok:#4ade80;
--ok-soft:#12301f;--warn:#fbbf24;--warn-soft:#33270e;--bad:#f87171;--bad-soft:#3a1616;
--on-brand:#0b0d11}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
a{color:var(--brand)}header{background:var(--surface);border-bottom:1px solid var(--line)}
.bar{max-width:1080px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;gap:16px;
flex-wrap:wrap}.logo{width:34px;height:34px;border-radius:9px;background:var(--brand);
color:var(--on-brand);display:grid;place-items:center;font-weight:700}
.brand{font-weight:650}.brand small{display:block;color:var(--muted);font-weight:400;
font-size:12px}nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto}
nav a{padding:7px 12px;border-radius:9px;text-decoration:none;color:var(--ink)}
nav a.on{background:var(--brand-soft);color:var(--brand);font-weight:600}
main{max-width:1080px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:4px 0 4px}h2{font-size:17px;margin:24px 0 10px}
.lead{color:var(--muted);margin:0 0 18px}.card{background:var(--surface);
border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.grow{flex:1;min-width:0}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;
font-weight:600;background:var(--brand-soft);color:var(--brand)}
.badge.escalated{background:var(--bad-soft);color:var(--bad)}
.badge.waiting_approval{background:var(--warn-soft);color:var(--warn)}
.badge.resolved{background:var(--ok-soft);color:var(--ok)}
.muted{color:var(--muted)}.small{font-size:13px}.mono{font-family:ui-monospace,Consolas,monospace;
font-size:12.5px}.ellipsis{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button,.btn{border:1px solid var(--line);background:var(--surface);color:var(--ink);
border-radius:10px;padding:8px 14px;font:inherit;cursor:pointer;text-decoration:none}
.btn.primary,button.primary{background:var(--brand);border-color:var(--brand);
color:var(--on-brand)}button.danger{color:var(--bad)}
input,textarea{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--surface);color:var(--ink);font:inherit;font-size:16px}
textarea{min-height:90px;resize:vertical}label{display:block;margin:10px 0 4px;font-weight:600}
.msgs{display:flex;flex-direction:column;gap:8px}.msg{max-width:80%;padding:9px 12px;
border-radius:14px;background:var(--bg)}.msg.customer{align-self:flex-start}
.msg.agent{align-self:flex-end;background:var(--brand-soft)}
.msg.human{align-self:flex-end;background:var(--ok-soft)}.who{font-size:11px;
color:var(--muted);margin-bottom:2px}ol.audit{list-style:none;padding:0;margin:0}
ol.audit li{border-left:3px solid var(--line);padding:6px 0 10px 12px;margin-left:4px}
ol.audit li b{display:block}.kpis{display:grid;grid-template-columns:repeat(auto-fit,
minmax(170px,1fr));gap:12px;margin-bottom:18px}.kpi{background:var(--surface);
border:1px solid var(--line);border-radius:14px;padding:16px}.kpi .n{font-size:30px;
font-weight:700;letter-spacing:-.02em}.kpi.big .n{color:var(--brand)}
.track{height:10px;border-radius:99px;background:var(--bg);overflow:hidden}
.fill{height:100%;border-radius:99px;background:var(--brand)}.fill.ok{background:var(--ok)}
.fill.warn{background:var(--warn)}.fill.bad{background:var(--bad)}
.type{display:grid;grid-template-columns:minmax(150px,1.3fr) 1fr 1fr;gap:14px;align-items:center}
@media (max-width:640px){.type{grid-template-columns:1fr}.msg{max-width:95%}}
.missing{margin:8px 0 0;padding-left:18px;color:var(--warn);font-size:13px}
form.sending button{opacity:.6;cursor:wait}
select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--surface);color:var(--ink);font:inherit;font-size:16px}
input[type=checkbox],input[type=radio]{width:auto}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
font-size:14px;vertical-align:top}
th{color:var(--muted);font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}
.steps{list-style:none;padding:0;margin:0}
.steps li{display:flex;gap:12px;align-items:baseline;padding:10px 0;
border-bottom:1px solid var(--line)}
.steps li:last-child{border:0}.badge.hecha{background:var(--ok-soft);color:var(--ok)}
.badge.fallo{background:var(--bad-soft);color:var(--bad)}
.badge.en_curso{background:var(--warn-soft);color:var(--warn)}
.alert{border-color:var(--bad);color:var(--bad)}.okmsg{border-color:var(--ok)}
pre{overflow:auto;background:var(--bg);padding:10px;border-radius:10px;font-size:12.5px}
.empty{text-align:center;color:var(--muted);padding:28px}footer{text-align:center;
color:var(--muted);font-size:12px;padding:12px}
nav a .count{display:inline-block;min-width:20px;height:20px;margin-left:6px;padding:0 6px;
border-radius:10px;background:var(--warn);color:var(--surface);font-size:12px;font-weight:700;
line-height:20px;text-align:center;vertical-align:1px}
.card.attention{border-color:var(--warn);box-shadow:0 0 0 3px var(--warn-soft)}
.toast{position:fixed;top:78px;left:50%;z-index:10;transform:translateX(-50%);
max-width:calc(100vw - 32px);padding:11px 18px;border-radius:12px;background:var(--ink);
color:var(--surface);font-weight:600;box-shadow:0 12px 32px -10px rgba(0,0,0,.45);
animation:toast 5s ease forwards}
@keyframes toast{0%{opacity:0;transform:translate(-50%,-8px)}6%,84%{opacity:1;
transform:translate(-50%,0)}100%{opacity:0;transform:translate(-50%,0);visibility:hidden}}
@media (prefers-reduced-motion:reduce){.toast{animation:none}}
"""

NAV = [
    ("casos", "Casos"),
    ("aprobaciones", "Aprobaciones"),
    ("onboarding", "Onboarding"),
    ("reporte", "Reporte"),
    ("configuracion", "Configuración"),
    ("metricas", "Métricas"),
    ("equipo", "Equipo"),
]
# Lo que una persona del equipo (rol `agent`) no ve ni puede abrir (decisión 058).
ADMIN_ONLY = {"onboarding", "configuracion", "equipo"}
# Solo el operador de la plataforma.
ADMIN_NAV = [("empresas", "Empresas"), ("facturacion", "Facturación")]
# Aprobaciones pendientes de la empresa de la sesión: `enter` lo fija en cada pedido.
PENDING: ContextVar[int] = ContextVar("panel_pending", default=0)

# Confirmación breve después de decidir una aprobación (`?aviso=…`). El texto sale de esta lista:
# lo que venga en la URL no se muestra.
NOTICES = {
    "reembolso-email": "Reembolso aprobado · se envió el email de confirmación",
    "reembolso-sin-email": "Reembolso aprobado · no se pudo enviar el email de confirmación",
    "reembolso": "Reembolso aprobado",
    "aprobado": "Solicitud aprobada",
    "fallo": "Aprobada, pero el sistema de la empresa no pudo completarla",
    "rechazado": "Solicitud rechazada",
    "borrador-enviado": "Respuesta enviada al cliente",
    "borrador-rechazado": "Borrador rechazado · no se envió nada",
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


def page(
    title: str,
    body: str,
    *,
    active: str = "",
    logged_in: bool = True,
    session: "Session | None" = None,
    refresh: int | None = None,
) -> str:
    """`session`: de ahí salen el nombre de la empresa del encabezado y el menú. `refresh`:
    segundos para volver a cargar la página sola (avance del onboarding)."""
    company = session.company if session and session.company else "Enterprise Data AI"
    admin = bool(session and session.admin)
    if english.english():  # el panel de una empresa en inglés (decisión 037)
        title = english.translate_text(title)
    items = NAV + (ADMIN_NAV if admin else [])
    if session is not None and session.role == "agent":
        items = [(slug, label) for slug, label in items if slug not in ADMIN_ONLY]
    waiting = PENDING.get()
    # Las aprobaciones pendientes se ven desde cualquier pantalla (demo en inglés, cambio D).
    count = (
        f'<span class="count" aria-label="{waiting} esperando aprobación">{waiting}</span>'
        if waiting
        else ""
    )
    links = "".join(
        f'<a href="/panel/{slug}" class="{"on" if slug == active else ""}">{label}'
        f"{count if slug == 'aprobaciones' else ''}</a>"
        for slug, label in items
    )
    nav = (
        f'<nav>{links}<form method="post" action="/panel/logout" style="margin:0">'
        '<button type="submit">Salir</button></form></nav>'
        if logged_in and session is not None
        else ""
    )
    footer = (
        "Demo de Enterprise Data AI · Acme Store es una tienda ficticia"
        if admin
        else "Enterprise Data AI"
    )
    reload = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    markup = f"""<!doctype html>
<html lang="{"en" if english.english() else "es"}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">{reload}
<title>{esc(title)} · {esc(company)}</title><style>{CSS}</style></head>
<body><header><div class="bar"><div class="logo">{esc(company[:1].upper())}</div>
<div class="brand">{esc(company)}<small>Panel del equipo</small></div>{nav}</div></header>
<main>{body}</main>
<footer>{footer}</footer>
<script>{NO_DOUBLE_SUBMIT}</script></body></html>"""
    return english.translate_html(markup) if english.english() else markup


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


RESOLVED_BY = {"agent": "por el asistente", "human": "con una persona"}
STATUS_EN = {
    "escalated": "Handed off",
    "waiting_approval": "Waiting for approval",
    "open": "Open",
    "resolved": "Resolved",
}
RESOLVED_BY_EN = {"agent": "by the assistant", "human": "with a person"}


def status_badge(status: str, resolved_by: str | None = None, reopened: bool = False) -> str:
    """Estado del caso. Un resuelto dice quién lo resolvió (la unidad de cobro es el caso
    resuelto por el asistente)."""
    en = english.english()
    statuses, who = (STATUS_EN, RESOLVED_BY_EN) if en else (STATUS, RESOLVED_BY)
    label = statuses.get(status, status)
    if status == "resolved" and resolved_by in who:
        label = f"{label} {who[resolved_by]}"
    if reopened:  # el cliente volvió por el mismo tema: ya no cuenta para el cobro
        label = f"{label} · {'reopened' if en else 'reabierto'}"
    return f'<span class="badge {esc(status)}">{esc(label)}</span>'
