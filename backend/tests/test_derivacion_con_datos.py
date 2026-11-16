"""Arreglo de la demo en inglés del 16/11/2026 — test de regresión (decisión 045).

En Render, después de verificar a la clienta y consultar NW-4521, la herramienta funcionó (los
controles del guardián en verde) pero gpt-oss-20b llamó a `derivar` ("Customer request for order
status") en lugar de dar el estado del pedido. Ningún control rechazó la respuesta: el modelo
eligió derivar.

- Si el agente deriva en un turno en el que una herramienta ya devolvió los datos, se le pide una
  vez que responda con esos datos. Si vuelve a derivar, se deriva.
- En "Behind the scenes", un caso derivado muestra el motivo en una línea, sin datos del cliente.
"""

import json
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack
from test_northwind import _without_the_demo_northwind

from app.agent import grounding, loop, presentable
from app.agent.loop import AgentResult
from app.audit.log import reconstruct_case
from app.channels.web.routes import WebDeps, web_deps
from app.demo import behind, northwind
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.main import app

SLUG = "northwind-outfitters"


def _late_order(stack: SimulatedStack) -> AgentResult:
    """El comienzo del guion: el pedido, el email y el código."""
    stack.add_contact("web")
    first = stack.send("web", "Where is my order NW-4521?")
    assert first.verification_requested
    stack.send("web", "My email is emily@example.com")
    return stack.send("web", stack.last_code())


def _events(stack: SimulatedStack, result: AgentResult) -> list[Any]:
    return list(reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id))


# -- 1. la regresión: responde el estado con los datos de la herramienta -----------------------
def test_tras_verificar_y_consultar_responde_el_estado_del_pedido(
    conn: psycopg.Connection,
) -> None:
    """Como gpt-oss-20b en Render: después de consultar el pedido, deriva. Se le pide responder
    con los datos y responde el estado de NW-4521."""
    stack = build_simulated_stack(conn, company="northwind", mode="deriva_tras_consultar")
    answer = _late_order(stack)

    assert answer.action == "responder", answer.message
    assert [(t.name, t.status) for t in answer.tool_calls] == [("consultar_pedido", "executed")]
    # Los datos del pedido, tal como los devolvió la herramienta.
    for fact in ("NW-4521", "UPS Ground", "1Z9A4F210377451236", "Denver", "November 14"):
        assert fact.lower() in answer.message.lower(), fact
    kinds = [event.kind for event in _events(stack, answer)]
    assert kinds.count("handoff_rejected") == 1
    assert "escalated" not in kinds


def test_si_insiste_en_derivar_deriva(conn: psycopg.Connection) -> None:
    """El control no le quita al agente la derivación: se la pide reconsiderar una sola vez."""
    stack = build_simulated_stack(conn, company="northwind", mode="deriva_siempre_tras_consultar")
    answer = _late_order(stack)

    assert answer.action == "derivar"
    events = _events(stack, answer)
    assert [e.kind for e in events].count("handoff_rejected") == 1
    escalated = next(e for e in events if e.kind == "escalated")
    assert escalated.payload["causa"] == {"tipo": "agente", "reconsiderada": True}


def test_sin_datos_de_una_herramienta_la_derivacion_no_se_reconsidera(
    conn: psycopg.Connection,
) -> None:
    """Sin una herramienta que haya devuelto datos (el pedido no es de la clienta), la
    derivación del agente sigue como antes, sin pedirle nada."""
    stack = build_simulated_stack(conn, company="northwind", mode="deriva_siempre_tras_consultar")
    stack.add_contact("web")
    stack.send("web", "Where is my order NW-4540?")  # de otro cliente
    stack.send("web", "My email is emily@example.com")
    answer = stack.send("web", stack.last_code())

    assert answer.action == "derivar"
    assert [t.status for t in answer.tool_calls] == ["denied"]
    kinds = [event.kind for event in _events(stack, answer)]
    assert "handoff_rejected" not in kinds


# -- 2. "Behind the scenes": el motivo de la derivación, en una línea --------------------------
@pytest.fixture
def web(conn: psycopg.Connection) -> Iterator[tuple[TestClient, SimulatedStack]]:
    import httpx
    from simulated.voyage import voyage_transport

    stack = build_simulated_stack(conn, company="northwind", mode="deriva_siempre_tras_consultar")
    embedder = VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )
    _without_the_demo_northwind(conn)
    northwind.ensure_northwind(conn, embedder, password="clave-de-northwind-demo")
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent
    )
    try:
        yield TestClient(app), stack
    finally:
        app.dependency_overrides.clear()


def test_el_panel_muestra_por_que_derivo(web: tuple[TestClient, SimulatedStack]) -> None:
    client, stack = web
    session = client.post(f"/chat/session?canal={SLUG}").json()["session"]
    for text in ("Where is my order NW-4521?", "My email is emily@example.com", None):
        reply = client.post(
            "/chat/messages", json={"session": session, "text": text or stack.last_code()}
        )
        assert reply.status_code == 200, reply.text
    data = client.get(f"/demo/{SLUG}/live", params={"session": session}).json()
    order = data["turns"][2]

    assert order["action"] == "derivar"
    assert order["handoff"] == (
        "The agent chose to hand off, even after being asked to answer with the tool's data"
    )
    assert [c["kind"] for c in order["checks"]] == ["handoff_rejected"]
    # Sin datos del cliente, ni el motivo que escribió el modelo (puede traer cualquier cosa).
    text = json.dumps(order, ensure_ascii=False)
    for personal in ("emily@example.com", "Emily", "Customer request for order status"):
        assert personal not in text, personal
    page = client.get(f"/demo/{SLUG}").text
    assert "Reconsidered a handoff" in page and "Why" in page


@pytest.mark.parametrize(
    ("cause", "line"),
    [
        (
            {"tipo": "fundamento", "detalle": "numero"},
            "Answer blocked by grounding check: a number not in the documents or tool results",
        ),
        (
            {"tipo": "fundamento", "detalle": "fecha"},
            "Answer blocked by grounding check: a date not in the documents or tool results",
        ),
        (
            {"tipo": "accion_sin_herramienta"},
            "Answer blocked by grounding check: it claimed an action no tool performed",
        ),
        (
            {"tipo": "formato", "detalle": "JSON"},
            "Answer blocked by format check: it had JSON",
        ),
        (
            {"tipo": "reverificar"},
            "Answer blocked: it asked a verified customer to verify again",
        ),
        ({"tipo": "pidio_persona"}, "The customer asked to talk to a person"),
        ({"tipo": "amenaza_legal"}, "Legal threat: a person always handles it"),
        ({"tipo": "agente", "reconsiderada": False}, "The agent chose to hand off"),
    ],
)
def test_cada_motivo_en_una_linea(cause: dict[str, Any], line: str) -> None:
    assert behind.handoff_line(cause, english=True) == line


def test_el_motivo_en_espanol() -> None:
    assert behind.handoff_line({"tipo": "fundamento", "detalle": "numero"}, english=False) == (
        "Respuesta frenada por el control de fundamento: un número que no está en los "
        "documentos ni en las herramientas"
    )
    assert behind.handoff_line(None, english=False) is None
    assert behind.handoff_line({"tipo": "desconocido"}, english=True) is None


def test_cada_causa_de_derivacion_tiene_su_linea() -> None:
    """Cada causa que registra el agente, con cada detalle posible, se muestra en los dos
    idiomas."""
    details = {"fundamento": grounding.KINDS, "formato": presentable.ISSUES}
    for kind in loop.HANDOFF_CAUSES:
        for detail in details.get(kind, (None,)):
            cause = {"tipo": kind, **({"detalle": detail} if detail else {})}
            for english in (True, False):
                assert behind.handoff_line(cause, english=english), (cause, english)
