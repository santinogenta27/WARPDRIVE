"""Hito 6, etapa 3 — observabilidad (decisión 028): la página Métricas, una traza de
OpenTelemetry por caso y alertas en los logs por latencia, costo por caso y errores."""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from decimal import Decimal

import psycopg
import pytest
import runner
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.obs import alerts, metrics, tracing
from app.panel.notify import notify_customer
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime

EMAIL, PASSWORD = "operador@plataforma.example", "clave-del-operador"


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    tracing.configure(exporter)
    try:
        yield exporter
    finally:
        tracing.reset()


@pytest.fixture(autouse=True)
def clean_alerts() -> None:
    alerts.reset()


@pytest.fixture
def client(
    conn: psycopg.Connection, stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("PANEL_EMAIL", EMAIL)
    monkeypatch.setenv("PANEL_PASSWORD", PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, tenant: Guardian(c, ToolRuntime(stack.acme)),
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def login(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> None:
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303


# -- trazas ----------------------------------------------------------------------------------
def test_cada_caso_es_una_traza_con_un_span_por_paso(
    stack: SimulatedStack, spans: InMemorySpanExporter
) -> None:
    first = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    second = stack.send("lucia", "¿Y abren los domingos?")
    assert second.case_id == first.case_id
    stack.close_open_cases()
    other = stack.send("martin", "Quiero el reembolso del pedido 1005")

    finished = spans.get_finished_spans()
    by_trace: dict[str, list[str]] = {}
    for span in finished:
        by_trace.setdefault(f"{span.context.trace_id:032x}", []).append(span.name)
    # Una traza por caso, y su id es el id del caso: los dos turnos de Lucía están juntos.
    assert set(by_trace) == {
        tracing.trace_id_for(first.case_id),
        tracing.trace_id_for(other.case_id),
    }
    lucia = by_trace[tracing.trace_id_for(first.case_id)]
    assert lucia.count("agent.turn") == 2
    assert lucia.count("busqueda") == 2 and lucia.count("herramienta") >= 1
    assert sum(name.startswith("llm ") for name in lucia) == 5  # 3 del primer turno y 2 del otro

    turn = next(s for s in finished if s.name == "agent.turn")
    assert turn.parent is None and turn.attributes is not None
    assert turn.attributes["case.id"] == str(first.case_id)
    assert turn.attributes["agent.action"] == "responder"
    assert turn.attributes["case.type"] == "donde_esta_mi_pedido"
    assert int(turn.attributes["db.queries"]) > 0  # type: ignore[arg-type]
    assert [(e.name, dict(e.attributes or {})) for e in turn.events] == [
        ("guardian.decision", {"tool.name": "consultar_pedido", "tool.status": "executed"})
    ]
    model = next(s for s in finished if s.name.startswith("llm "))
    assert model.parent is not None and model.parent.span_id == turn.context.span_id
    assert model.attributes is not None and int(model.attributes["llm.input_tokens"]) > 0  # type: ignore[arg-type]
    assert model.start_time is not None and turn.start_time is not None
    assert turn.start_time <= model.start_time <= (model.end_time or 0) <= (turn.end_time or 0)

    # Ningún span lleva el texto del cliente ni datos de su pedido.
    everything = " ".join(
        f"{s.name} {dict(s.attributes or {})} {[dict(e.attributes or {}) for e in s.events]}"
        for s in finished
    )
    for private in ("1001", "1005", "domingos", "reembolso del pedido", "Av. "):
        assert private not in everything, private


def test_sin_exportador_configurado_no_se_traza_y_el_agente_responde_igual(
    stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("OTEL_TRACES_CONSOLE", raising=False)
    tracing.reset()
    assert stack.send("lucia", "¿Abren los domingos?").action == "responder"
    assert tracing.configure() is None


# -- alertas ---------------------------------------------------------------------------------
def test_alerta_en_los_logs_cuando_la_latencia_pasa_el_umbral(
    stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="app.alerts"):
        stack.send("lucia", "¿Abren los domingos?")
    assert caplog.records == []  # con los umbrales de la spec, un turno normal no avisa

    monkeypatch.setenv("ALERT_LATENCY_MS", "1")
    with caplog.at_level("WARNING", logger="app.alerts"):
        result = stack.send("lucia", "¿Tienen cuotas sin interés?")
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and lines[0].startswith("ALERTA latencia ")
    assert f"caso={result.case_id}" in lines[0] and "umbral_ms=1" in lines[0]
    assert re.search(r"paso_mas_lento=(modelo|base|busqueda|herramientas)", lines[0])


def test_alerta_una_sola_vez_cuando_el_costo_de_un_caso_cruza_el_umbral() -> None:
    tenant, case = uuid.uuid4(), uuid.uuid4()
    summary = {
        "total_ms": 900,
        "modelo_ms": 500,
        "base_ms": 300,
        "busqueda_ms": 100,
        "herramientas_ms": 0,
        "llamadas": {"herramientas": 0},
    }

    def check(total: str, turn: str) -> list[str]:
        return alerts.check_turn(
            tenant, case, summary, case_cost=Decimal(total), turn_cost=Decimal(turn)
        )

    assert check("0.30", "0.30") == []
    crossed = check("0.55", "0.25")  # este turno cruza USD 0,50
    assert len(crossed) == 1 and crossed[0].startswith("ALERTA costo_por_caso ")
    assert "umbral_usd=0.5" in crossed[0] and f"caso={case}" in crossed[0]
    assert check("0.70", "0.15") == []  # ya había avisado


def test_alerta_cuando_los_errores_de_una_empresa_llegan_al_umbral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALERT_ERRORS", "3")
    tenant, saved = uuid.uuid4(), []
    fired: list[str] = []
    for second in range(4):
        fired += alerts.record_error(
            tenant, "chat_web", persist=lambda t, k, d: saved.append((t, k, d)), now=1000.0 + second
        )
    assert len(saved) == 4 and saved[0] == (tenant, "chat_web", "")
    assert len(fired) == 1 and fired[0].startswith("ALERTA errores ") and "errores=3" in fired[0]
    # Fuera de la ventana, los errores viejos no cuentan.
    assert alerts.record_error(tenant, "chat_web", now=1000.0 + 5000) == []


def test_un_error_que_corta_una_respuesta_del_chat_queda_registrado(
    conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    address = f"web:test-{uuid.uuid4().hex}"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    saved: list[tuple[uuid.UUID, str, str]] = []

    def broken(_: psycopg.Connection) -> None:
        raise RuntimeError("se cayó el modelo")

    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn),
        agent=broken,  # type: ignore[arg-type]
        address=address,
        record_error=lambda tenant, kind, detail: saved.append((tenant, kind, detail)),
    )
    try:
        api = TestClient(app)
        session = api.post("/chat/session").json()["session"]
        answer = api.post("/chat/messages", json={"session": session, "text": "hola"}).text
    finally:
        app.dependency_overrides.clear()
    assert "event: error" in answer
    assert saved == [(stack.seed.tenant_id, "chat_web", "RuntimeError")]  # sin texto del cliente


# -- métricas --------------------------------------------------------------------------------
def test_el_respaldo_entre_proveedores_queda_en_el_uso(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, primary_out_of_quota=True)
    stack.send("lucia", "¿Abren los domingos?")
    data = metrics.collect(conn, stack.seed.tenant_id)
    assert data["modelo"]["llamadas"] == 2 and data["modelo"]["respaldos"] >= 1
    assert data["modelo"]["rechazos"] == 0


def test_la_pagina_de_metricas_muestra_resolucion_latencia_errores_y_costos(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    tenant = stack.seed.tenant_id
    resolved = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    stack.send("lucia", "gracias")  # resuelto por el asistente
    escalated = stack.send("martin", "quiero hablar con una persona")
    notify_customer(conn, tenant, escalated.case_id, "Hola, soy Ana.")
    stack.send("martin", "listo, gracias")  # resuelto con una persona
    stack.add_contact("abierto")
    stack.send("abierto", "¿Abren los domingos?")  # sigue abierto
    with tenant_scope(conn, tenant):
        conn.execute("update usage set cost_usd = 0")  # un solo costo conocido en la página
        conn.execute(
            "update usage set cost_usd = 0.0123 where id = (select id from usage"
            " where case_id = %s and kind = 'llm' limit 1)",
            (resolved.case_id,),
        )
        conn.execute(
            "insert into error_events (tenant_id, kind, detail) values (%s, 'chat_web', 'x')",
            (tenant,),
        )
    metrics.record_eval_run(
        conn,
        tenant,
        mode="groq",
        prompt_version="system_v4",
        model="groq:openai/gpt-oss-20b",
        scores={"resolution_rate": 0.93, "judge_mean": 0.88, "critical_failures": 0, "cases": 14},
        results=[],
    )

    data = metrics.collect(conn, tenant)
    assert data["casos"]["total"] == 3 and data["casos"]["derivados"] == 1
    assert data["casos"]["resueltos_asistente"] == 1 and data["casos"]["resueltos_persona"] == 1
    assert data["casos"]["tasa_resolucion"] == 0.5
    assert data["casos"]["tasa_derivacion"] == pytest.approx(1 / 3)
    assert data["latencia"]["turnos"] == 5
    assert 0 < data["latencia"]["p50_ms"] <= data["latencia"]["p95_ms"]
    assert data["por_caso"][0]["caso"] == resolved.case_id  # el de más costo va primero
    assert data["por_caso"][0]["costo_usd"] == Decimal("0.012300")
    assert sum(d["llamadas"] for d in data["por_dia"]) == data["modelo"]["llamadas"] > 0
    assert {m["uso"] for m in data["por_modelo"]} == {"llm", "embedding"}

    assert client.get("/panel/metricas", follow_redirects=False).status_code == 303  # sin login
    login(client)
    page = client.get("/panel/metricas").text
    for text in (
        "50%",  # resolución
        "33%",  # derivación
        "latencia p50",
        "latencia p95",
        "respondió el proveedor de respaldo",
        "El chat web no pudo responder",
        "USD 0.0123",
        "Por día",
        "Por modelo",
        "claude-sonnet-5-5",
        str(resolved.case_id)[:8],
        tracing.trace_id_for(resolved.case_id),
        "system_v4",
        "groq:openai/gpt-oss-20b",
        "93%",
        "0,88",
        "Para el cobro",
    ):
        assert text in page, text
    assert ">Métricas<" in page


def test_las_metricas_de_una_empresa_no_incluyen_las_de_otra(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    answered = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    metrics.record_eval_run(
        conn,
        stack.seed.tenant_id,
        mode="groq",
        prompt_version="system_v4",
        model="modelo-de-acme",
        scores={"resolution_rate": 1.0},
        results=[],
    )
    norte = create_company(conn, "Tienda Norte", "ana@tiendanorte.example", "clave-de-norte-1")
    empty = metrics.collect(conn, norte.tenant_id)
    assert empty["casos"]["total"] == 0 and empty["latencia"]["turnos"] == 0
    assert empty["modelo"]["llamadas"] == 0 and empty["por_caso"] == [] and empty["evals"] == []

    login(client, "ana@tiendanorte.example", "clave-de-norte-1")
    page = client.get("/panel/metricas").text
    assert "Tienda Norte" in page and "Todavía no hay uso" in page
    for leak in (str(answered.case_id)[:8], "modelo-de-acme", "claude-sonnet"):
        assert leak not in page, leak


def test_los_evals_guardan_su_corrida_en_el_historial(
    conn: psycopg.Connection, stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = runner.Report(
        mode="groq", resolution_rate=0.93, judge_mean=0.9, critical_failures=[], cases=[]
    )
    # Sin empresa de demo en la base (el CI), no guarda nada.
    monkeypatch.setattr("app.demo.acme.find_demo_tenant", lambda c: None)
    assert runner.record_history("groq", report, "groq:modelo", conn) is False
    monkeypatch.setattr("app.demo.acme.find_demo_tenant", lambda c: stack.seed.tenant_id)
    assert runner.record_history("groq", report, "groq:modelo", conn) is True
    saved = metrics.collect(conn, stack.seed.tenant_id)["evals"]
    assert [(e["modo"], e["prompt"], e["modelo"], e["resolucion"]) for e in saved] == [
        ("groq", "system_v4", "groq:modelo", 0.93)
    ]
