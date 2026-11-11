"""Panel, página Métricas (decisión 028): resolución y derivación, latencia p50 y p95, errores
y respaldos entre proveedores, tokens y costo por día, por modelo y por caso, y el historial de
evals por versión de prompt y modelo. Siempre de la empresa de la sesión.
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.obs import metrics
from app.obs.tracing import trace_id_for
from app.panel.html import esc, local_time, page
from app.panel.routes import Deps, enter

router = APIRouter(prefix="/panel", include_in_schema=False)

ERROR_KINDS = {
    "chat_web": "El chat web no pudo responder",
    "cuota": "Sin cuota del modelo",
}
USES = {"llm": "Modelo", "embedding": "Embeddings"}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _seconds(milliseconds: int | None) -> str:
    return "—" if milliseconds is None else f"{milliseconds / 1000:.1f} s".replace(".", ",")


def _usd(value: Any) -> str:
    amount = Decimal(str(value or 0))
    return f"USD {amount:.4f}" if amount else "USD 0"


def _int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _kpi(value: str, label: str, big: bool = False) -> str:
    return (
        f'<div class="kpi{" big" if big else ""}"><div class="n">{esc(value)}</div>'
        f'<div class="muted">{esc(label)}</div></div>'
    )


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(
        f'<th class="{"num" if h.startswith(">") else ""}">{esc(h.lstrip(">"))}</th>'
        for h in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"num" if headers[i].startswith(">") else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def render(data: dict[str, Any], company: str) -> str:
    cases, latency, model = data["casos"], data["latencia"], data["modelo"]
    billing, errors = data["cobro"], data["errores"]
    steps = latency["promedio_ms"]
    slowest = max(steps, key=lambda k: steps[k] or 0) if latency["turnos"] else None
    step_names = {
        "modelo": "modelo",
        "base": "base de datos",
        "busqueda": "búsqueda",
        "herramientas": "herramientas",
    }
    step_row = " · ".join(f"{step_names[k]} {_seconds(steps[k])}" for k in step_names)
    cut = sum(e["cantidad"] for e in errors["turnos_cortados"])
    error_rows = [
        [esc(ERROR_KINDS.get(e["tipo"], e["tipo"])), str(e["cantidad"]), local_time(e["ultimo"])]
        for e in errors["turnos_cortados"]
    ]
    day_rows = [
        [
            d["dia"].strftime("%d/%m"),
            _int(d["llamadas"]),
            _int(d["tokens_entrada"]),
            _int(d["tokens_salida"]),
            _usd(d["costo_usd"]),
        ]
        for d in data["por_dia"]
    ]
    model_rows = [
        [
            f'<span class="mono">{esc(m["modelo"])}</span>',
            esc(USES.get(m["uso"], m["uso"])),
            _int(m["llamadas"]),
            _int(m["tokens_entrada"]),
            _int(m["tokens_salida"]),
            _usd(m["costo_usd"]),
        ]
        for m in data["por_modelo"]
    ]
    case_rows = [
        [
            f'<a class="mono" href="/panel/casos/{c["caso"]}">{esc(str(c["caso"])[:8])}</a>',
            _int(c["llamadas"]),
            _int(c["tokens"]),
            _usd(c["costo_usd"]),
            f'<span class="mono small">{trace_id_for(c["caso"])}</span>',
        ]
        for c in data["por_caso"]
    ]
    eval_rows = [
        [
            local_time(e["fecha"], "%d/%m/%Y %H:%M"),
            esc(e["modo"]),
            f'<span class="mono">{esc(e["prompt"])}</span>',
            f'<span class="mono">{esc(e["modelo"])}</span>',
            _pct(e["resolucion"]),
            "—" if e["juez"] is None else f"{float(e['juez']):.2f}".replace(".", ","),
            "—" if e["criticas"] is None else str(e["criticas"]),
        ]
        for e in data["evals"]
    ]
    days = data["dias"]
    return f"""<h1>Métricas</h1><p class="lead">Cómo viene atendiendo el asistente de
{esc(company)} en los últimos {days} días.</p>
<div class="kpis">{_kpi(_pct(cases["tasa_resolucion"]), "resueltos por el asistente", True)}
{_kpi(_pct(cases["tasa_derivacion"]), "derivados a una persona")}
{_kpi(_seconds(latency["p50_ms"]), "latencia p50 por respuesta")}
{_kpi(_seconds(latency["p95_ms"]), "latencia p95 por respuesta")}</div>

<h2>Resolución y derivación</h2><div class="card">
<div class="kpis" style="margin:0">{_kpi(str(cases["total"]), "casos")}
{_kpi(str(cases["resueltos_asistente"]), "resueltos por el asistente")}
{_kpi(str(cases["resueltos_persona"]), "resueltos con una persona")}
{_kpi(str(cases["derivados"]), "derivados")}{_kpi(str(cases["en_curso"]), "en curso")}
{_kpi(str(cases["reabiertos"]), "reabiertos")}</div>
<p class="muted small">Resolución: de los casos ya resueltos, los que cerró el asistente sin una
persona. Derivación: de todos los casos, los que pasaron a una persona.</p>
<p><b>Para el cobro:</b> {billing["cobrables"]} cobrables · {billing["en_ventana"]} dentro de los
7 días (todavía pueden reabrirse) · {billing["con_persona"]} con una persona ·
{billing["reabiertos"]} reabiertos.</p></div>

<h2>Latencia</h2><div class="card"><p>{latency["turnos"]} respuestas medidas. La mitad tarda
menos de <b>{_seconds(latency["p50_ms"])}</b> y el 95%, menos de
<b>{_seconds(latency["p95_ms"])}</b>.</p>
<p class="muted small">Promedio por paso: {esc(step_row)}.
{f"Donde más tiempo se va: {step_names[slowest]}." if slowest else ""}</p></div>

<h2>Errores y respaldos entre proveedores</h2><div class="card">
<div class="kpis" style="margin:0">{_kpi(_int(model["llamadas"]), "llamadas al modelo")}
{_kpi(str(model["respaldos"]), "respondió el proveedor de respaldo")}
{_kpi(str(model["rechazos"]), "sin respuesta válida del modelo")}
{_kpi(str(model["recuperadas"]), "llamadas recuperadas")}
{_kpi(str(errors["herramientas_fallidas"]), "herramientas que fallaron")}
{_kpi(str(cut), "respuestas cortadas por un error")}</div>
{_table(["Error", ">Veces", "Último"], error_rows, "Ninguna respuesta se cortó por un error.")}
</div>

<h2>Tokens y costo</h2><div class="card"><p>Costo por caso: promedio
<b>{_usd(data["costo_por_caso"]["promedio_usd"])}</b>, máximo
<b>{_usd(data["costo_por_caso"]["maximo_usd"])}</b>.</p>
<h3>Por día (últimos 14)</h3>
{
        _table(
            ["Día", ">Llamadas", ">Tokens de entrada", ">Tokens de salida", ">Costo"],
            day_rows,
            "Todavía no hay uso.",
        )
    }
<h3>Por modelo</h3>
{
        _table(
            ["Modelo", "Uso", ">Llamadas", ">Tokens de entrada", ">Tokens de salida", ">Costo"],
            model_rows,
            "Todavía no hay uso.",
        )
    }
<h3>Por caso (los 10 de más costo)</h3>
{
        _table(
            ["Caso", ">Llamadas", ">Tokens", ">Costo", "Traza (OpenTelemetry)"],
            case_rows,
            "Todavía no hay casos con uso.",
        )
    }
</div>

<h2>Historial de evals</h2><div class="card">
{
        _table(
            ["Fecha", "Modo", "Prompt", "Modelo", ">Resolución", ">Juez", ">Fallas críticas"],
            eval_rows,
            "Todavía no hay corridas de evals guardadas.",
        )
    }
<p class="muted small">Cada corrida de los evals queda con la versión del prompt y el modelo que
respondió: así se ve si un cambio mejoró o empeoró.</p></div>"""


@router.get("/metricas", response_class=HTMLResponse)
def metrics_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        data = metrics.collect(conn, ctx.tenant_id)
    body = render(data, ctx.session.company)
    return HTMLResponse(page("Métricas", body, active="metricas", session=ctx.session))
