"""Probar antes de salir y salir en vivo (decisión 056).

- `GET /panel/probar`: el playground. El chat de la empresa y, al lado, cada paso del caso
  ("detrás de escena"), sin tener que activar esa vista pública.
- `GET /panel/probar/live`: los pasos de una sesión del chat de la empresa, para el playground.
- `POST /panel/salir-en-vivo`: sale en vivo (si el reporte pasa el umbral) o pausa.
"""

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.channels.web import widget
from app.channels.web.channel import _TOKEN
from app.companies import go_live
from app.companies.create import chat_slug
from app.demo import behind
from app.demo.behind_routes import _session_tenant, render
from app.panel import english
from app.panel.home_routes import PAUSE
from app.panel.html import esc
from app.panel.routes import Ctx, Deps, Form, enter

router = APIRouter(prefix="/panel", include_in_schema=False)


@router.get("/probar", response_class=HTMLResponse)
def playground(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        slug = chat_slug(conn, ctx.tenant_id)
        found = widget.load(conn, ctx.tenant_id)
    if slug is None:
        return RedirectResponse("/panel/test", status_code=303)
    page = render(
        name=found.name,
        slug=slug,
        accent=found.accent,
        language=found.language,
        northwind=False,
        live="/panel/probar/live",
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@router.get("/probar/live")
def playground_live(request: Request, deps: Deps, session: str = "") -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not _TOKEN.match(session):
        return JSONResponse({"detail": "Sesión inválida"}, status_code=401)
    with deps.connection() as conn:
        if _session_tenant(conn, session) != ctx.tenant_id:
            return JSONResponse({"detail": "Sesión inválida"}, status_code=401)
        data = behind.steps(conn, ctx.tenant_id, session)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


def readiness_html(ctx: Ctx, found: go_live.Readiness) -> str:
    """La tarjeta del reporte: probar, el umbral y el botón para salir en vivo."""
    en = english.english()
    if found.live:
        state = "En vivo: el agente atiende en tu sitio." if not en else "Live on your site."
        button = (
            f'<form method="post" action="/panel/salir-en-vivo" {PAUSE}><input type="hidden"'
            f' name="csrf" value="{ctx.csrf}"><input type="hidden" name="activo" value="no">'
            f'<button type="submit">{"Pause" if en else "Pausar"}</button></form>'
        )
    else:
        state = (
            "Todavía no está en vivo: el chat atiende en el panel y en su página de prueba."
            if not en
            else "Not live yet: the chat works in the dashboard and on its test page."
        )
        disabled = "" if found.ready else " disabled"
        button = (
            f'<form method="post" action="/panel/salir-en-vivo"><input type="hidden" name="csrf"'
            f' value="{ctx.csrf}"><input type="hidden" name="activo" value="si">'
            f'<button class="primary" type="submit"{disabled}>'
            f"{'Go live' if en else 'Salir en vivo'}</button></form>"
        )
    shown = list(found.reasons)
    if en:  # los motivos, en el idioma del panel (decisión 037)
        shown = []
        if found.resolution is None:
            shown.append("The readiness report is missing: upload your history and run it.")
        elif found.resolution < found.threshold:
            shown.append(
                f"It would resolve {found.resolution:.0%} on its own; the minimum is "
                f"{found.threshold:.0%}. Check what's missing in the report."
            )
        if not found.attacks_ok:
            shown.append("Some attacks or improper requests weren't stopped: check the report.")
    reasons = "".join(f"<li>{esc(r)}</li>" for r in shown) if not found.live else ""
    minimum = f"{found.threshold:.0%}"
    rule = (
        f"Go live needs at least {minimum} estimated resolution and every attack stopped."
        if en
        else f"Para salir hace falta al menos {minimum} de resolución estimada y frenar todos "
        "los ataques."
    )
    try_it = "Try your agent" if en else "Probar el agente"
    missing = f'<ul class="small">{reasons}</ul>' if reasons else ""
    simulated = (  # la última simulación (decisión 080): informativa
        f'<div class="small">{"Simulation" if en else "Simulación"}: pass^3 '
        f"{found.simulation:.0%}</div>"
        if found.simulation is not None
        else ""
    )
    return f"""<div class="card"><div class="row"><div class="grow"><b>{html.escape(state)}</b>
<div class="muted small">{html.escape(rule)}</div>{simulated}{missing}
</div><a class="button" href="/panel/probar" target="_blank" rel="noopener">{try_it}</a>
{button}</div></div>"""


@router.post("/salir-en-vivo")
def toggle_live(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    on = form.get("activo") == "si"
    try:
        with deps.connection() as conn:
            go_live.set_live(conn, ctx.tenant_id, on)
    except go_live.GoLiveError:
        return RedirectResponse("/panel/test", status_code=303)
    back = form.get("volver", "")
    return RedirectResponse(back if back.startswith("/panel/") else "/panel/test", status_code=303)
