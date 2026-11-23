"""Resultados medibles en el panel (Hito 12, tarea 6; decisión 083).

- `GET /panel/resultados?mes=AAAA-MM`: el informe mensual de resolución autónoma, el análisis de
  conversaciones de los últimos 30 días y el informe semanal por email.
- `GET /panel/resultados/export?mes=AAAA-MM`: el informe del mes en CSV, un renglón por caso.
- `POST /panel/resultados/informe-semanal`: activa o desactiva el email semanal (admins).
"""

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.cases.billing import current_time
from app.demo.behind import HANDOFF_LINES
from app.outcomes import analysis, autonomy, weekly
from app.panel import english
from app.panel.html import esc, page, toast
from app.panel.labels import type_label
from app.panel.routes import Ctx, Deps, Form, _forbidden, enter

router = APIRouter(prefix="/panel", include_in_schema=False)
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _month(value: str | None) -> tuple[int, int]:
    found = _MONTH.match(value or "")
    if found and 1 <= int(found.group(2)) <= 12:
        return int(found.group(1)), int(found.group(2))
    now = current_time()
    return now.year, now.month


def _can_configure(ctx: Ctx) -> bool:
    return ctx.session.admin or ctx.session.role != "agent"


def _lang() -> str:
    return "en" if english.english() else "es"


def _examples(items: list[str]) -> str:
    if not items:
        return ""
    return '<ul class="small muted">' + "".join(f"<li>{esc(t)}</li>" for t in items) + "</ul>"


def _analysis_html(found: dict[str, Any]) -> str:
    topics = "".join(
        f"<tr><td>{esc(type_label(t['tipo']))}{_examples(t['ejemplos'])}</td>"
        f"<td>{t['casos']}</td></tr>"
        for t in found["temas"]
    )
    lines = HANDOFF_LINES[_lang()]
    reasons = "".join(
        f"<tr><td>{esc(lines.get(d['causa'], d['causa']))}{_examples(d['ejemplos'])}</td>"
        f"<td>{d['casos']}</td></tr>"
        for d in found["derivaciones"]
    )
    groups = "".join(
        f"<tr><td><b>{esc(g['tema']) or '<span>Otras</span>'}</b>{_examples(g['ejemplos'])}</td>"
        f"<td>{g['cantidad']}</td></tr>"
        for g in found["sin_respuesta"]["grupos"]
    )
    empty = '<p class="muted">Sin datos en estos 30 días.</p>'
    return f"""<div class="card"><h2>Temas principales</h2>{
        f'<table class="small"><tbody>{topics}</tbody></table>' if topics else empty
    }</div>
<div class="card"><h2>Motivos de derivación</h2>{
        f'<table class="small"><tbody>{reasons}</tbody></table>' if reasons else empty
    }</div>
<div class="card"><h2>Preguntas sin respuesta</h2>
<p class="muted small">Lo que el asistente no pudo responder, agrupado por la palabra que más se
repite. Sirve para saber qué documento cargar. Los ejemplos no tienen datos personales.</p>{
        f'<table class="small"><tbody>{groups}</tbody></table>' if groups else empty
    }</div>"""


@router.get("/resultados", response_class=HTMLResponse)
def results_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    year, month = _month(request.query_params.get("mes"))
    with deps.connection() as conn:
        report = autonomy.month(conn, ctx.tenant_id, year, month)
        found = analysis.analyze(conn, ctx.tenant_id)
        weekly_on = weekly.enabled(conn, ctx.tenant_id)
    labels = autonomy.LABELS[_lang()]
    counts = report.counts
    rows = "".join(
        f"<tr><td>{esc(labels[state])}</td><td>{counts[state]}</td></tr>"
        for state in autonomy.STATES
    )
    period = f"{year:04d}-{month:02d}"
    toggle = (
        f"""<form method="post" action="/panel/resultados/informe-semanal">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<button name="activo" value="{"no" if weekly_on else "si"}"
 class="{"" if weekly_on else "primary"}">
{"Desactivar" if weekly_on else "Activar"}</button></form>"""
        if _can_configure(ctx)
        else ""
    )
    body = f"""{toast(request.query_params.get("aviso"))}<h1>Resultados</h1>
<p class="lead">Lo que resolvió el asistente, medido de forma auditable, y de qué hablan tus
clientes.</p>
<div class="card"><div class="row"><h2 class="grow" style="margin:0">Resolución autónoma</h2>
<form method="get" action="/panel/resultados" class="row" style="gap:6px;margin:0">
<input type="month" name="mes" value="{period}" aria-label="Mes" style="max-width:170px">
<button type="submit">Ver</button></form>
<a class="button" href="/panel/resultados/export?mes={period}">Exportar CSV</a></div>
<p class="muted small">Un caso es una resolución autónoma si nunca se derivó, lo resolvió el
asistente, no se reabrió dentro de las 72 h y el cliente no dijo que no se resolvió. El CSV trae
un renglón por caso, sin datos del cliente: con el número de caso se audita en Casos.</p>
<div class="kpis"><div class="kpi"><div class="n">{report.autonomous}</div>
<div class="muted">resoluciones autónomas</div></div>
<div class="kpi"><div class="n">{report.total}</div><div class="muted">casos resueltos</div></div>
<div class="kpi"><div class="n">{report.rate:.0%}</div><div class="muted">tasa (sin los casos en
ventana)</div></div></div>
<table class="small"><tbody>{rows}</tbody></table></div>
<h2>Análisis de conversaciones (últimos 30 días)</h2>{_analysis_html(found)}
<div class="card"><div class="row"><div class="grow"><h2 style="margin:0">Informe semanal</h2>
<span class="badge {"resolved" if weekly_on else ""}">{"Activado" if weekly_on else "Desactivado"}
</span><p class="muted small">Cada lunes, un email a los admins con la semana anterior:
conversaciones, resoluciones autónomas, derivaciones, CSAT, temas y preguntas sin
respuesta.</p></div>{toggle}</div></div>"""
    return HTMLResponse(page("Resultados", body, active="resultados", session=ctx.session))


@router.get("/resultados/export")
def export_results(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    year, month = _month(request.query_params.get("mes"))
    with deps.connection() as conn:
        report = autonomy.month(conn, ctx.tenant_id, year, month)
    name = f"resolucion-autonoma-{year:04d}-{month:02d}.csv"
    return Response(
        autonomy.to_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/resultados/informe-semanal")
def toggle_weekly(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not _can_configure(ctx):
        return _forbidden("Esta acción es solo para los admins de la empresa.")
    with deps.connection() as conn:
        weekly.set_enabled(conn, ctx.tenant_id, form.get("activo") == "si")
    return RedirectResponse("/panel/resultados", status_code=303)
