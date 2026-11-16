"""Etapa 0 del hito 6: cuánto tarda cada paso de una respuesta (base, modelo, búsqueda y
herramientas) y un tope de viajes a la base por turno, que es donde más pesaba la latencia
(decisión 025)."""

import httpx
import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.audit import log as audit_log
from app.db.connection import tenant_scope
from app.llm.groq import GroqTransport
from app.obs import timing
from app.rag.search import hybrid_search

# Viajes a Postgres por turno, medidos con evals/latency.py. Antes de la decisión 025 eran 115
# (consulta simple) y 144 (con herramienta); ahora son 28 y 35. El tope deja margen, pero frena
# una regresión grande.
MAX_QUERIES_SIMPLE = 40
MAX_QUERIES_WITH_TOOL = 50


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_el_tiempo_se_cuenta_al_paso_mas_interno() -> None:
    clock = FakeClock()
    with timing.turn(clock) as timer:
        clock.now += 1  # nada abierto: otros
        with timing.step(timing.MODEL):
            clock.now += 2
            with timing.db_step():  # la base dentro del modelo cuenta como base
                clock.now += 3
            clock.now += 4
        with timing.step(timing.SEARCH):
            clock.now += 5
            with timing.db_step():  # dentro de la búsqueda, el tiempo es de la búsqueda
                clock.now += 6
    summary = timer.summary()
    assert summary["total_ms"] == 21000
    assert summary["modelo_ms"] == 6000 and summary["base_ms"] == 3000
    assert summary["busqueda_ms"] == 11000 and summary["herramientas_ms"] == 0
    assert summary["otros_ms"] == 1000
    assert summary["llamadas"] == {
        "base": 2,
        "modelo": 1,
        "espera": 0,
        "busqueda": 1,
        "herramientas": 0,
        "verificaciones": 0,
        "email": 0,
    }


def test_sin_un_turno_abierto_medir_no_hace_nada() -> None:
    with timing.step(timing.MODEL), timing.db_step():
        pass
    assert timing.active() is None


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def _timing(stack: SimulatedStack, case_id: object) -> dict[str, object]:
    events = audit_log.reconstruct_case(stack.conn, stack.seed.tenant_id, case_id)  # type: ignore[arg-type]
    found = [e.payload for e in events if e.kind == "turn_timing"]
    assert len(found) == 1
    return found[0]


def test_cada_turno_registra_cuanto_tardo_cada_paso(stack: SimulatedStack) -> None:
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    measured = _timing(stack, result.case_id)
    for key in ("total_ms", "base_ms", "modelo_ms", "busqueda_ms", "herramientas_ms", "otros_ms"):
        assert isinstance(measured[key], int) and measured[key] >= 0, key
    calls = measured["llamadas"]
    assert isinstance(calls, dict)
    # Clasificador + agente (pide la herramienta y después responde), una búsqueda y la API.
    assert calls["modelo"] == 3 and calls["busqueda"] == 1 and calls["herramientas"] >= 1
    assert 0 < calls["base"] <= MAX_QUERIES_WITH_TOOL
    parts = sum(int(measured[f"{k}_ms"]) for k in (*timing.STEPS, "otros"))  # type: ignore[call-overload]
    assert abs(parts - int(measured["total_ms"])) <= 5  # type: ignore[call-overload]
    assert audit_log.verify_chain(stack.conn, stack.seed.tenant_id, result.case_id).ok


def test_una_consulta_simple_hace_pocos_viajes_a_la_base(stack: SimulatedStack) -> None:
    result = stack.send("lucia", "¿Abren los domingos?")
    calls = _timing(stack, result.case_id)["llamadas"]
    assert isinstance(calls, dict) and 0 < calls["base"] <= MAX_QUERIES_SIMPLE


# -- Demo en inglés: cada paso del turno, en el log y en "detrás de escena" (decisión 044) ----
def test_cada_llamada_y_cada_paso_quedan_en_el_detalle(
    stack: SimulatedStack, capsys: pytest.CaptureFixture[str]
) -> None:
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    measured = _timing(stack, result.case_id)
    steps = measured["pasos"]
    assert isinstance(steps, list)
    for step in steps:
        assert isinstance(step["ms"], int) and step["ms"] >= 0, step
    names = [(s["paso"], s["nombre"]) for s in steps]
    # Cada llamada al modelo, con su papel: el clasificador y las dos del agente.
    assert [n for p, n in names if p == "modelo"] == ["clasificador", "agente", "agente"]
    assert names[0] == ("modelo", "clasificador")
    assert ("busqueda", "rag") in names
    assert ("herramientas", "GET /orders/{order_id}") in names
    assert ("verificaciones", "controles de la respuesta") in names
    assert all("espera_ms" in s for s in steps if s["paso"] == "modelo")
    calls = measured["llamadas"]
    assert isinstance(calls, dict) and calls["verificaciones"] >= 1

    # Una línea por turno en el log del servidor, sin el texto del cliente.
    lines = [line for line in capsys.readouterr().err.splitlines() if "[Latencia]" in line]
    assert len(lines) == 1
    line = lines[0]
    assert "clasificador" in line and "agente" in line and "consultas" in line
    assert "GET /orders/{order_id}" in line and "verificaciones" in line
    assert "Dónde" not in line and "pedido" not in line


def test_la_espera_por_el_limite_de_groq_se_cuenta_aparte() -> None:
    """Groq gratis pide esperar a que se renueven los tokens del minuto: esa espera es parte
    de la llamada al modelo, pero se muestra aparte (no es tiempo de generación)."""
    clock = FakeClock()
    answers = iter(
        [
            {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "5s"},
            {"x-ratelimit-remaining-tokens": "7000", "x-ratelimit-reset-tokens": "1s"},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        clock.now += 0.5  # medio segundo de generación
        return httpx.Response(200, headers=next(answers), json={"choices": [], "usage": {}})

    def sleep(seconds: float) -> None:
        clock.now += seconds

    transport = GroqTransport(
        api_key="gsk_test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_tokens=4000,
        sleep=sleep,
        clock=clock,
    )
    body = {"model": "openai/gpt-oss-20b", "messages": []}
    with timing.turn(clock) as timer:
        for _ in range(2):
            with timing.step(timing.MODEL, "modelo"):
                transport.post(body)
    summary = timer.summary()
    assert summary["espera_ms"] == 5000 and summary["modelo_ms"] == 1000
    assert summary["llamadas"]["espera"] == 1
    first, second = [s for s in summary["pasos"] if s["paso"] == "modelo"]
    assert (first["ms"], first["espera_ms"]) == (500, 0)
    assert (second["ms"], second["espera_ms"]) == (5500, 5000)
    assert "espera de Groq 5.0 s" in timing.describe(summary)


def test_la_busqueda_hace_un_solo_viaje_a_la_base(stack: SimulatedStack) -> None:
    """Las dos listas de candidatos (vector y texto) y los fragmentos salen en una consulta;
    el otro viaje es el registro del uso del embedding."""
    tenant = stack.seed.tenant_id
    with tenant_scope(stack.conn, tenant), timing.turn() as timer:
        found = hybrid_search(stack.conn, tenant, stack.embedder, "¿Abren los domingos?", k=5)
    assert found and found[0].source == "horarios_y_sucursales"
    assert timer.calls["base"] == 2
