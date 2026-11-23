"""Pruebas gratis en el panel del operador (decisión 075): para hacer el seguimiento de quién
probó, cuánto lo usó y si pidió el piloto o hablar."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.panel.html import esc, local_time, page
from app.panel.routes import Deps, _forbidden, enter
from app.trial import service

router = APIRouter(prefix="/panel", include_in_schema=False)
STATUSES = {
    "code_sent": "Esperando el código",
    "building": "Armándose",
    "ready": "Lista",
    "failed": "No se pudo leer el sitio",
    "converted": "Convertida",
    "expired": "Borrada (venció)",
}


def _when(value: object) -> str:
    return local_time(value, "%d/%m %H:%M") if value else "—"  # type: ignore[arg-type]


def _asked(label: str, value: object) -> str:
    return f"{label} · {_when(value)}" if value else "—"


@router.get("/operator/trials", response_class=HTMLResponse)
def trials_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden("Solo el operador de la plataforma ve las pruebas.")
    with deps.connection() as conn:
        trials = service.operator_rows(conn)
    rows = "".join(
        f"<tr><td><b>{esc(t.domain)}</b><br><span class='muted small'>{esc(t.site_url)}</span>"
        f"</td><td>{esc(t.email)}</td><td>{esc(STATUSES.get(t.status, t.status))}</td>"
        f"<td>{t.messages}/{service.MAX_MESSAGES}</td>"
        f"<td>{_asked('Pidió el piloto', t.pilot_requested_at)}</td>"
        f"<td>{_asked('Pidió hablar', t.contact_requested_at)}</td>"
        f"<td>{_when(t.created_at)}</td></tr>"
        for t in trials
    )
    table = (
        "<table><tr><th>Sitio</th><th>Email</th><th>Estado</th><th>Mensajes</th>"
        f"<th>Piloto</th><th>Hablar con Santino</th><th>Pedida</th></tr>{rows}</table>"
        if rows
        else '<p class="muted">Todavía nadie pidió una prueba.</p>'
    )
    body = f"""<h1>Pruebas gratis</h1><p class="lead">Quién probó el asistente con su tienda en
<span class="mono">/probar</span>. Una prueba que no pasa a piloto se borra a los
{service.TRIAL_DAYS} días; esta lista queda para el seguimiento.</p>
<div class="card" style="overflow-x:auto">{table}</div>"""
    return HTMLResponse(page("Pruebas", body, active="operator/trials", session=ctx.session))
