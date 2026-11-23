"""Simulaciones en el panel (Hito 12, tarea 3; decisión 080).

- La tarjeta "Simulaciones" del Reporte: el botón, el estado de la última corrida y su resultado
  pass^3 por tipo de caso, con las corridas que fallaron y el motivo del juez.
- `POST /panel/simulaciones`: pide una simulación de 20 conversaciones (solo admins). Corre en
  segundo plano, en el worker de la API.
"""

from typing import Any

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.panel import english
from app.panel.html import esc, local_time
from app.panel.labels import type_label
from app.panel.routes import Ctx, Deps, Form, _forbidden, enter
from app.simulation import runner as simulation

router = APIRouter(prefix="/panel", include_in_schema=False)
MAX_FAILURES_SHOWN = 10
TONES_EN = {"enojado": "angry", "confundido": "confused", "apurado": "in a hurry"}


def _can_run(ctx: Ctx) -> bool:
    return ctx.session.admin or ctx.session.role != "agent"


def _percent(value: object) -> str:
    return f"{float(value or 0):.0%}"  # type: ignore[arg-type]


def _tone(tone: str) -> str:
    return TONES_EN.get(tone, tone) if english.english() else tone


def simulation_card(ctx: Ctx, conn: psycopg.Connection) -> str:
    latest = simulation.latest(conn, ctx.tenant_id)
    done = simulation.latest_done(conn, ctx.tenant_id)
    state = ""
    if latest is not None and latest.status in ("queued", "running"):
        state = (
            '<p><span class="badge">En curso</span> <span>Las conversaciones simuladas corren en '
            "segundo plano. Volvé a abrir el Reporte en unos minutos.</span></p>"
        )
    elif latest is not None and latest.status == "failed":
        state = '<p><span class="badge escalated">La última simulación falló.</span></p>'
    button = (
        f"""<form method="post" action="/panel/simulaciones">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<button class="primary" type="submit">Simular 20 conversaciones</button></form>"""
        if _can_run(ctx) and not state.startswith('<p><span class="badge">En curso')
        else ""
    )
    result = ""
    if done is not None and done.summary:
        summary = done.summary
        rows = "".join(
            f"<tr><td>{esc(type_label(t['tipo']))}</td><td>{int(t['clientes'])}</td>"
            f"<td>{_percent(t['pass3'])}</td><td>{_percent(t['pass1'])}</td></tr>"
            for t in summary.get("tipos") or []
        )
        failures = [c for c in done.conversations if not c.get("paso")][:MAX_FAILURES_SHOWN]
        personas = {p["id"]: p for p in done.personas}

        def line(c: dict[str, Any]) -> str:
            tone = _tone(str(personas.get(c["cliente"], {}).get("tono", "")))
            reason = esc(c["juez"].get("motivo", ""))
            return (
                f"<li>{esc(type_label(c.get('tipo')))} · {esc(tone)} · <span>corrida</span>"
                f" {int(c.get('corrida') or 0)}: {reason}</li>"
            )

        failed = "".join(line(c) for c in failures)
        result = f"""<p><b>pass^3: {_percent(summary.get("pass3"))}</b> ·
<span>pass^1:</span> {_percent(summary.get("pass1"))} ·
{int(summary.get("clientes") or 0)} <span>clientes, 3 corridas cada uno</span> ·
<span class="muted">{local_time(done.updated_at)}</span></p>
<table class="small"><thead><tr><th>Tipo de caso</th><th>Clientes</th><th>pass^3</th>
<th>pass^1</th></tr></thead><tbody>{rows}</tbody></table>
{f'<h3>Corridas que fallaron</h3><ul class="small">{failed}</ul>' if failed else ""}"""
    return f"""<div class="card"><h2>Simulaciones</h2>
<p class="muted small">Clientes simulados (enojados, confundidos o apurados) creados desde tus
documentos, tipos de caso y reglas conversan con el asistente. Cada conversación se repite 3
veces y un juez revisa si el cliente logró su objetivo, si se respetaron las reglas y la marca y si
la respuesta fue correcta. pass^3: los clientes que pasaron las 3 veces. No toca tu sistema ni deja
datos.</p>{state}{result}{button}</div>"""


@router.post("/simulaciones")
def start_simulation(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not _can_run(ctx):
        return _forbidden("Esta acción es solo para los admins de la empresa.")
    with deps.connection() as conn:
        latest = simulation.latest(conn, ctx.tenant_id)
        if latest is None or latest.status not in ("queued", "running"):
            simulation.request(conn, ctx.tenant_id, requested_by=ctx.session.email)
    deps.kick_worker()
    return RedirectResponse("/panel/reporte", status_code=303)
