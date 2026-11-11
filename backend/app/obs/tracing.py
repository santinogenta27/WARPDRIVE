"""Trazas con OpenTelemetry: una traza por caso (spec, "Observabilidad"; decisión 028).

Cada turno del agente es un span `agent.turn` con un span hijo por llamada al modelo, por
búsqueda y por herramienta. El id de la traza es el id del caso: todos los turnos de un caso,
aunque pasen días entre uno y otro, quedan en la misma traza, y con el id del caso (el que
muestra el panel) se la encuentra en cualquier visor.

Los spans se arman al terminar el turno, con los tiempos que ya midió `app/obs/timing.py`: el
agente no cambia y, sin exportador configurado, esto no hace nada.

Los atributos no llevan datos personales ni texto del cliente: nombres de modelos y de
herramientas, cantidades, estados y costos.

Configuración (variables estándar de OpenTelemetry):

- `OTEL_EXPORTER_OTLP_ENDPOINT` (y `OTEL_EXPORTER_OTLP_HEADERS` si el destino pide una clave):
  envía las trazas por OTLP/HTTP a cualquier backend compatible.
- `OTEL_TRACES_CONSOLE=1`: las escribe en los logs (para verlas sin un backend).
"""

import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import Status, StatusCode

from app.obs.timing import Timer

logger = logging.getLogger(__name__)
SERVICE = "enterprise-data-ai"
_case_trace: ContextVar[int | None] = ContextVar("case_trace", default=None)
_provider: TracerProvider | None = None
_configured = False


class CaseIdGenerator(RandomIdGenerator):
    """El id de la traza es el id del caso (los dos son de 128 bits)."""

    def generate_trace_id(self) -> int:
        forced = _case_trace.get()
        return forced if forced else super().generate_trace_id()


def trace_id_for(case_id: uuid.UUID) -> str:
    """El id de la traza de un caso, como lo muestra un visor (32 caracteres hexadecimales)."""
    return f"{case_id.int:032x}"


def configure(exporter: SpanExporter | None = None) -> TracerProvider | None:
    """Arma el proveedor de trazas. `exporter`: uno propio (tests). Sin él, según el entorno;
    si no hay nada configurado, devuelve None y no se traza."""
    global _provider, _configured
    _configured = True
    processors: list[Any] = []
    if exporter is not None:
        processors.append(SimpleSpanProcessor(exporter))
    else:
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        ):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            processors.append(BatchSpanProcessor(OTLPSpanExporter()))
        if os.environ.get("OTEL_TRACES_CONSOLE", "").strip() == "1":
            processors.append(SimpleSpanProcessor(ConsoleSpanExporter()))
    if not processors:
        _provider = None
        return None
    provider = TracerProvider(
        resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", SERVICE)}),
        id_generator=CaseIdGenerator(),
    )
    for processor in processors:
        provider.add_span_processor(processor)
    _provider = provider
    return provider


def reset() -> None:
    """Vuelve al estado sin configurar (tests)."""
    global _provider, _configured
    _provider, _configured = None, False


def export_turn(
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    timer: Timer,
    *,
    action: str,
    case_type: str | None,
    tools: list[tuple[str, str]],
) -> None:
    """Exporta el turno como spans de la traza del caso. `tools`: (herramienta, decisión del
    guardián) de cada llamada del turno, en orden. Nunca lanza: un problema con las trazas no
    puede cortar una respuesta."""
    if not _configured:
        configure()
    if _provider is None:
        return
    try:
        _export(tenant_id, case_id, timer, action, case_type, tools)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo exportar la traza del caso %s", case_id)


def _export(
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    timer: Timer,
    action: str,
    case_type: str | None,
    tools: list[tuple[str, str]],
) -> None:
    assert _provider is not None
    tracer = _provider.get_tracer("app.agent")
    summary = timer.summary()
    token = _case_trace.set(case_id.int)
    try:
        root = tracer.start_span(
            "agent.turn",
            context=trace.Context(),  # sin padre: la raíz del turno, en la traza del caso
            start_time=timer.started_ns,
            attributes={
                "tenant.id": str(tenant_id),
                "case.id": str(case_id),
                "case.type": case_type or "",
                "agent.action": action,
                "db.queries": int(summary["llamadas"]["base"]),
                "db.ms": int(summary["base_ms"]),
                "turn.total_ms": int(summary["total_ms"]),
            },
        )
    finally:
        _case_trace.reset(token)
    parent = trace.set_span_in_context(root)
    pending = list(tools)
    for record in timer.records:
        attributes = {k: v for k, v in record["attrs"].items() if v not in (None, "")}
        name = str(record["name"])
        if record["kind"] == "modelo":
            name = f"llm {attributes.get('llm.model', '')}".strip()
        span = tracer.start_span(
            name, context=parent, start_time=record["start_ns"], attributes=attributes
        )
        if record["kind"] == "modelo" and "rechazo" in str(attributes.get("llm.events", "")):
            span.set_status(Status(StatusCode.ERROR, "rechazo del modelo"))
        span.end(end_time=record["end_ns"])
    # Las decisiones del guardián: una herramienta puede hacer más de un pedido HTTP (control
    # de dueño y ejecución) o ninguno (negada), así que van como eventos del turno.
    for tool, status in pending:
        root.add_event("guardian.decision", {"tool.name": tool, "tool.status": status})
    if action == "derivar":
        root.set_attribute("agent.escalated", True)
    root.end(end_time=timer.finished_ns)
