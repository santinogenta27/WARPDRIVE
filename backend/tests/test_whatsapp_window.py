"""Tarea 12d — test de aceptación: una respuesta humana a las 25 h sale como plantilla.

WhatsApp solo permite mensajes libres hasta 24 h después del último mensaje del cliente; fuera
de esa ventana se manda la plantilla `caso_actualizado` y el texto queda pendiente hasta que el
cliente responde.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent.loop import Agent
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.handler import WhatsAppDeps, handle_message_job
from app.channels.whatsapp.outbound import reply_as_human
from app.db.connection import tenant_scope
from app.jobs.queue import Job

PHONE_NUMBER_ID = "106540352242922"
CUSTOMER = "5491155550000"


class FakeGraph:
    def __init__(self, fail_text_with: int | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._fail_text_with = fail_text_with

    def client(self) -> WhatsAppClient:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body["type"] == "text" and self._fail_text_with:
                error = {"code": self._fail_text_with, "message": "Re-engagement message"}
                return httpx.Response(400, json={"error": error})
            self.sent.append(body)
            return httpx.Response(200, json={"messages": [{"id": f"wamid.out.{len(self.sent)}"}]})

        return WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    stack = build_simulated_stack(conn)
    conn.execute(
        "update tenants set settings = settings || %s where id = %s",
        (Jsonb({"whatsapp": {"phone_number_id": PHONE_NUMBER_ID}}), stack.seed.tenant_id),
    )
    return stack


def _deps(stack: SimulatedStack, graph: FakeGraph) -> WhatsAppDeps:
    return WhatsAppDeps(agent=lambda c: stack.agent, client=graph.client(), token="token-meta")


def _customer_writes(
    conn: psycopg.Connection, deps: WhatsAppDeps, text: str, hours_ago: float, wamid: str
) -> None:
    payload = {
        "phone_number_id": PHONE_NUMBER_ID,
        "wamid": wamid,
        "sender": CUSTOMER,
        "name": "Ana",
        "timestamp": int(time.time() - hours_ago * 3600),
        "type": "text",
        "text": text,
    }
    row = conn.execute(
        "insert into jobs (kind, payload) values ('whatsapp_message', %s) returning id",
        (Jsonb(payload),),
    ).fetchone()
    assert row is not None
    handle_message_job(conn, Job(row[0], "whatsapp_message", payload, 1, 5), deps)


def _case_id(conn: psycopg.Connection, stack: SimulatedStack) -> Any:
    with tenant_scope(conn, stack.seed.tenant_id):
        row = conn.execute("select id from cases order by created_at desc limit 1").fetchone()
    assert row is not None
    return row[0]


def test_respuesta_humana_a_las_25_h_sale_como_plantilla(
    conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    graph = FakeGraph()
    deps = _deps(stack, graph)
    _customer_writes(conn, deps, "Quiero hablar con una persona", hours_ago=25, wamid="wamid.1")
    case_id = _case_id(conn, stack)
    graph.sent.clear()

    delivery = reply_as_human(
        conn, stack.seed.tenant_id, case_id, "Ya te devolvimos la plata.", deps=deps
    )

    assert delivery.kind == "template"
    sent = graph.sent[-1]
    assert sent["type"] == "template" and sent["to"] == CUSTOMER
    assert sent["template"]["name"] == "caso_actualizado"
    params = sent["template"]["components"][0]["parameters"]
    assert params == [{"type": "text", "text": "Ana"}]
    assert "Ya te devolvimos" not in json.dumps(sent)  # el texto libre todavía no sale

    # El cliente responde la plantilla: se envía el mensaje pendiente y no se llama al agente.
    def no_agent(c: psycopg.Connection) -> Agent:
        raise AssertionError("el agente no debe responder a la respuesta de la plantilla")

    deps.agent = no_agent
    _customer_writes(conn, deps, "Hola", hours_ago=0, wamid="wamid.2")
    assert graph.sent[-1]["type"] == "text"
    assert graph.sent[-1]["text"]["body"] == "Ya te devolvimos la plata."
    with tenant_scope(conn, stack.seed.tenant_id):
        pending = conn.execute(
            "select count(*) from outbound_messages where status = 'pending'"
        ).fetchone()
        roles = conn.execute(
            "select role from case_messages where case_id = %s order by created_at", (case_id,)
        ).fetchall()
    assert pending == (0,)
    assert [r[0] for r in roles][-1] == "human"


def test_respuesta_humana_dentro_de_la_ventana_sale_como_texto(
    conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    graph = FakeGraph()
    deps = _deps(stack, graph)
    _customer_writes(conn, deps, "Quiero hablar con una persona", hours_ago=23, wamid="wamid.3")
    case_id = _case_id(conn, stack)

    delivery = reply_as_human(conn, stack.seed.tenant_id, case_id, "Hola, soy Juan.", deps=deps)

    assert delivery.kind == "text"
    assert graph.sent[-1]["text"]["body"] == "Hola, soy Juan."


def test_si_meta_rechaza_por_ventana_usa_la_plantilla(
    conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    graph = FakeGraph()
    deps = _deps(stack, graph)
    _customer_writes(conn, deps, "Quiero hablar con una persona", hours_ago=1, wamid="wamid.4")
    case_id = _case_id(conn, stack)
    deps.client = FakeGraph(fail_text_with=131047).client()  # Meta dice que pasaron 24 h

    now = datetime.now(UTC) + timedelta(minutes=1)
    delivery = reply_as_human(conn, stack.seed.tenant_id, case_id, "Novedades", deps=deps, now=now)
    assert delivery.kind == "template"
