"""Etapa 0 del hito 6: cuánto tarda cada paso de una respuesta (base, modelo, búsqueda y
herramientas) y un tope de viajes a la base por turno, que es donde más pesaba la latencia
(decisión 025)."""

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.audit import log as audit_log
from app.obs import timing

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
    assert summary["llamadas"] == {"base": 2, "modelo": 1, "busqueda": 1, "herramientas": 0}


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
