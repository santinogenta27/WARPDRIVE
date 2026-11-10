"""HTML del panel: layout común y ayudas. Páginas servidas por la API, sin recursos externos
(decisión 022). Todo texto que viene de la base o del cliente pasa por `esc`."""

import html
from datetime import datetime, timedelta, timezone

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
.empty{text-align:center;color:var(--muted);padding:28px}footer{text-align:center;
color:var(--muted);font-size:12px;padding:12px}
"""

NAV = [("casos", "Casos"), ("aprobaciones", "Aprobaciones"), ("reporte", "Reporte de preparación")]


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


# Hora de Buenos Aires: Argentina no tiene horario de verano. Con un desplazamiento fijo no hace
# falta la base de zonas horarias (en Windows `zoneinfo` necesita el paquete tzdata).
ARGENTINA = timezone(timedelta(hours=-3))


def local_time(value: datetime | str, fmt: str = "%d/%m %H:%M") -> str:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(ARGENTINA).strftime(fmt)


def page(title: str, body: str, *, active: str = "", logged_in: bool = True) -> str:
    links = "".join(
        f'<a href="/panel/{slug}" class="{"on" if slug == active else ""}">{label}</a>'
        for slug, label in NAV
    )
    nav = (
        f'<nav>{links}<form method="post" action="/panel/logout" style="margin:0">'
        '<button type="submit">Salir</button></form></nav>'
        if logged_in
        else ""
    )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)} · Acme Store</title><style>{CSS}</style></head>
<body><header><div class="bar"><div class="logo">A</div>
<div class="brand">Acme Store<small>Panel del equipo</small></div>{nav}</div></header>
<main>{body}</main>
<footer>Demo de Enterprise Data AI · Acme Store es una tienda ficticia</footer>
<script>{NO_DOUBLE_SUBMIT}</script></body></html>"""


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


def status_badge(status: str, resolved_by: str | None = None) -> str:
    """Estado del caso. Un resuelto dice quién lo resolvió (la unidad de cobro es el caso
    resuelto por el asistente)."""
    label = STATUS.get(status, status)
    if status == "resolved" and resolved_by in RESOLVED_BY:
        label = f"{label} {RESOLVED_BY[resolved_by]}"
    return f'<span class="badge {esc(status)}">{esc(label)}</span>'
