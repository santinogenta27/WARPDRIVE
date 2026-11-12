"""Panel del operador: facturación por período (tarea 22 de la spec, decisión 032).

Casos resueltos por empresa y por mes según la regla de cobro, el detalle caso por caso y la
exportación a CSV. Solo el operador de la plataforma entra acá: son datos de todas las empresas.
"""

import csv
import io
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.cases import billing
from app.companies.create import slugify
from app.panel.html import esc, page
from app.panel.labels import type_label
from app.panel.routes import Ctx, Deps, _forbidden, _not_found, enter

router = APIRouter(prefix="/panel", include_in_schema=False)

MONTHS = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)
STATE_LABELS = {
    "cobrable": ("resolved", "Se cobra"),
    "en_ventana": ("waiting_approval", "Dentro de los 7 días"),
    "con_persona": ("", "Con una persona"),
    "reabierto": ("escalated", "Reabierto"),
}
BILLED = {"cobrable": "sí", "en_ventana": "todavía no", "con_persona": "no", "reabierto": "no"}
RESOLVERS = {"agent": "asistente", "human": "persona"}
ONLY_OPERATOR = "Solo el operador de la plataforma ve la facturación."
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _month(value: str | None) -> tuple[int, int]:
    """`2026-11` -> (2026, 11). Vacío o mal escrito: el mes actual."""
    match = _MONTH.match(value or "")
    if match and 2000 <= int(match.group(1)) <= 2100 and 1 <= int(match.group(2)) <= 12:
        return int(match.group(1)), int(match.group(2))
    today = billing.current_time()
    return today.year, today.month


def _key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def _shift(year: int, month: int, delta: int) -> str:
    index = year * 12 + (month - 1) + delta
    return _key(index // 12, index % 12 + 1)


def _title(year: int, month: int) -> str:
    return f"{MONTHS[month - 1]} de {year}"


def _when(moment: datetime, fmt: str = "%d/%m %H:%M") -> str:
    return moment.astimezone(billing.ARGENTINA).strftime(fmt)


def _operator(request: Request, deps: Deps) -> Ctx | Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden(ONLY_OPERATOR)
    return ctx


def _switcher(year: int, month: int, base: str) -> str:
    return (
        f'<div class="row" style="margin-bottom:14px">'
        f'<a class="btn" href="{base}?mes={_shift(year, month, -1)}">← Mes anterior</a>'
        f'<b class="grow" style="text-align:center">{_title(year, month)}</b>'
        f'<a class="btn" href="{base}?mes={_shift(year, month, 1)}">Mes siguiente →</a></div>'
    )


RULE = (
    '<p class="muted small">Se cobra un caso que resolvió el asistente sin que intervenga una '
    "persona y que el cliente no reabrió en 7 días. Aprobar un reembolso no cuenta como "
    "intervención. Un caso entra en el mes en que se resolvió; los que tienen menos de 7 días "
    "todavía pueden reabrirse, así que el mes queda cerrado el día 8 del mes siguiente.</p>"
)


def _csv(name: str, header: list[str], rows: list[list[str]]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        # Con BOM: Excel abre el archivo con las tildes bien.
        ("﻿" + buffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/facturacion", response_class=HTMLResponse)
def billing_page(request: Request, deps: Deps, mes: str = "") -> Response:
    ctx = _operator(request, deps)
    if isinstance(ctx, Response):
        return ctx
    year, month = _month(mes)
    key = _key(year, month)
    with deps.connection() as conn:
        companies = billing.period_by_company(conn, year, month)
    rows = "".join(
        f'<tr><td><a href="/panel/facturacion/{c.tenant_id}?mes={key}"><b>{esc(c.name)}</b></a>'
        f'</td><td class="num"><b>{c.counts["cobrables"]}</b></td>'
        f'<td class="num">{c.counts["en_ventana"]}</td>'
        f'<td class="num">{c.counts["con_persona"]}</td>'
        f'<td class="num">{c.counts["reabiertos"]}</td>'
        f'<td class="num">{sum(c.counts.values())}</td></tr>'
        for c in companies
    )
    total = sum(c.counts["cobrables"] for c in companies)
    pending = sum(c.counts["en_ventana"] for c in companies)
    body = f"""<div class="row"><h1 class="grow">Facturación</h1>
<a class="btn" href="/panel/facturacion/exportar.csv?mes={key}">Exportar CSV</a></div>
<p class="lead">Casos resueltos por empresa y por mes. La unidad de cobro es el caso que resolvió
el asistente.</p>{_switcher(year, month, "/panel/facturacion")}
<div class="kpis"><div class="kpi big"><div class="n">{total}</div>
<div class="muted">casos a cobrar</div></div>
<div class="kpi"><div class="n">{pending}</div>
<div class="muted">dentro de los 7 días (todavía pueden reabrirse)</div></div></div>
<div class="card"><table><tr><th>Empresa</th><th class="num">A cobrar</th>
<th class="num">Dentro de los 7 días</th><th class="num">Con una persona</th>
<th class="num">Reabiertos</th><th class="num">Resueltos en total</th></tr>{rows}</table>
{RULE}</div>"""
    return HTMLResponse(page("Facturación", body, active="facturacion", session=ctx.session))


@router.get("/facturacion/exportar.csv")
def billing_export(request: Request, deps: Deps, mes: str = "") -> Response:
    ctx = _operator(request, deps)
    if isinstance(ctx, Response):
        return ctx
    year, month = _month(mes)
    with deps.connection() as conn:
        companies = billing.period_by_company(conn, year, month)
    header = [
        "empresa",
        "id_de_empresa",
        "mes",
        "casos_a_cobrar",
        "dentro_de_los_7_dias",
        "con_una_persona",
        "reabiertos",
        "resueltos_en_total",
    ]
    rows = [
        [c.name, str(c.tenant_id), _key(year, month)]
        + [str(c.counts[state]) for state in billing.STATES]
        + [str(sum(c.counts.values()))]
        for c in companies
    ]
    return _csv(f"facturacion-{_key(year, month)}.csv", header, rows)


def _company(deps: Deps, tenant_id: uuid.UUID) -> str | None:
    # Fuera de un tenant: el operador mira una empresa que no es la de su sesión.
    with deps.connection() as conn:
        row = conn.execute(
            "select name from tenants where id = %s and status = 'active'", (tenant_id,)
        ).fetchone()
    return str(row[0]) if row else None


@router.get("/facturacion/{tenant_id}", response_class=HTMLResponse)
def billing_detail(tenant_id: uuid.UUID, request: Request, deps: Deps, mes: str = "") -> Response:
    ctx = _operator(request, deps)
    if isinstance(ctx, Response):
        return ctx
    name = _company(deps, tenant_id)
    if name is None:
        return _not_found("Esa empresa", ctx.session)
    year, month = _month(mes)
    key = _key(year, month)
    with deps.connection() as conn:
        counts = billing.period_summary(conn, tenant_id, year, month)
        cases = billing.period_cases(conn, tenant_id, year, month)
    rows = "".join(
        f'<tr><td class="mono">{esc(str(c.case_id)[:8])}</td>'
        f"<td>{esc(type_label(c.case_type) if c.case_type else '—')}</td>"
        f"<td>{_when(c.created_at)}</td><td>{_when(c.closed_at)}</td>"
        f"<td>{esc(RESOLVERS.get(c.resolved_by or '', '—'))}</td>"
        f'<td><span class="badge {STATE_LABELS[c.state][0]}">{STATE_LABELS[c.state][1]}</span>'
        "</td></tr>"
        for c in cases
    )
    table = (
        "<table><tr><th>Caso</th><th>Tipo</th><th>Abierto</th><th>Resuelto</th>"
        f"<th>Resuelto por</th><th>Cobro</th></tr>{rows}</table>"
        if rows
        else '<p class="muted">No hay casos resueltos en este mes.</p>'
    )
    base = f"/panel/facturacion/{tenant_id}"
    body = f"""<p><a href="/panel/facturacion?mes={key}">← Todas las empresas</a></p>
<div class="row"><h1 class="grow">{esc(name)}</h1>
<a class="btn" href="{base}/exportar.csv?mes={key}">Exportar CSV</a></div>
<p class="lead">Casos resueltos en el mes, uno por renglón.</p>{_switcher(year, month, base)}
<div class="kpis"><div class="kpi big"><div class="n">{counts["cobrables"]}</div>
<div class="muted">casos a cobrar</div></div>
<div class="kpi"><div class="n">{counts["en_ventana"]}</div>
<div class="muted">dentro de los 7 días</div></div>
<div class="kpi"><div class="n">{counts["con_persona"]}</div>
<div class="muted">con una persona</div></div>
<div class="kpi"><div class="n">{counts["reabiertos"]}</div>
<div class="muted">reabiertos</div></div></div>
<div class="card">{table}{RULE}</div>"""
    return HTMLResponse(page("Facturación", body, active="facturacion", session=ctx.session))


@router.get("/facturacion/{tenant_id}/exportar.csv")
def billing_detail_export(
    tenant_id: uuid.UUID, request: Request, deps: Deps, mes: str = ""
) -> Response:
    ctx = _operator(request, deps)
    if isinstance(ctx, Response):
        return ctx
    name = _company(deps, tenant_id)
    if name is None:
        return _not_found("Esa empresa", ctx.session)
    year, month = _month(mes)
    with deps.connection() as conn:
        cases = billing.period_cases(conn, tenant_id, year, month)
    header = ["caso", "tipo_de_caso", "abierto", "resuelto", "resuelto_por", "estado", "se_cobra"]
    stamp = "%Y-%m-%d %H:%M"
    rows = [
        [
            str(c.case_id),
            c.case_type or "",
            _when(c.created_at, stamp),
            _when(c.closed_at, stamp),
            RESOLVERS.get(c.resolved_by or "", ""),
            c.state,
            BILLED[c.state],
        ]
        for c in cases
    ]
    return _csv(f"facturacion-{slugify(name)}-{_key(year, month)}.csv", header, rows)
