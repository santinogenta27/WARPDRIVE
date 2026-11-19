"""Tarea 12b — test de aceptación: un caso guionado se resuelve igual por web y por WhatsApp.

El chat web es otro `Channel`: mismo agente, guardián y auditoría, con el tenant Acme. Las
respuestas llegan por Server-Sent Events y cada sesión tiene un límite de 20 mensajes. Sin login:
para datos de cuenta, el cliente se verifica con el código que llega al email.
"""

import json
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.base import Channel
from app.channels.web.channel import WebChannel
from app.channels.web.routes import MESSAGE_LIMIT, WebDeps, client_ip, web_deps
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.handler import WhatsAppDeps, handle_message_job
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.db.connection import tenant_scope
from app.jobs.queue import Job
from app.main import app
from app.panel.notify import notify_customer

SCRIPTED = {
    case["id"]: case
    for case in json.loads(
        (Path(__file__).resolve().parents[2] / "evals/fixtures/acme/scripted_cases.json").read_text(
            encoding="utf-8"
        )
    )
}


@pytest.fixture
def address() -> str:
    """Dirección propia del test: la de la demo (DEMO_ADDRESS) puede existir en la base."""
    return f"web:test-{uuid.uuid4().hex}"


@pytest.fixture
def stack(conn: psycopg.Connection, address: str) -> SimulatedStack:
    stack = build_simulated_stack(conn)
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    return stack


@pytest.fixture
def api(conn: psycopg.Connection, stack: SimulatedStack, address: str) -> Iterator[TestClient]:
    # La transacción del test se revierte al final: el chat no confirma nada.
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent, address=address
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def new_session(api: TestClient) -> str:
    response = api.post("/chat/session")
    assert response.status_code == 201
    body = response.json()
    assert body["remaining"] == MESSAGE_LIMIT == 20
    session: str = body["session"]
    return session


def chat(
    api: TestClient, session: str, text: str, *, ip: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """Manda un mensaje y devuelve los eventos SSE (evento, datos) en orden. `ip`: la del
    cliente, como la agrega el proxy de Render a X-Forwarded-For."""
    headers = {"X-Forwarded-For": f"{ip}, 10.0.0.7"} if ip else {}
    response = api.post("/chat/messages", json={"session": session, "text": text}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def reply(events: list[tuple[str, dict[str, Any]]]) -> str:
    return " ".join(data["text"] for name, data in events if name == "message")


def final_actions(conn: psycopg.Connection, stack: SimulatedStack) -> list[dict[str, Any]]:
    with tenant_scope(conn, stack.seed.tenant_id):
        rows = conn.execute(
            "select payload from audit_events where kind in ('final_action', 'tool_call')"
            " order by seq"
        ).fetchall()
    return [r[0] for r in rows]


def test_web_cumple_la_interfaz_channel() -> None:
    channel: Channel = WebChannel()
    assert channel.kind == "web"


def test_un_caso_guionado_se_resuelve_igual_por_web_y_por_whatsapp(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    case = SCRIPTED["01_donde_esta_mi_pedido"]
    expected = case["esperado"]

    # Por WhatsApp: Lucía escribe desde su teléfono, que ya está verificado.
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id="PN1", token="t")
    sent: list[str] = []

    def graph(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"]["body"])
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    payload = {
        "phone_number_id": "PN1",
        "wamid": f"wamid.{uuid.uuid4().hex}",
        "sender": stack.seed.phones["lucia"].lstrip("+"),
        "timestamp": int(time.time()),
        "type": "text",
        "text": case["mensaje"],
    }
    row = conn.execute(
        "insert into jobs (kind, payload) values ('whatsapp_message', %s) returning id",
        (Jsonb(payload),),
    ).fetchone()
    assert row is not None
    client = WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(graph)))
    handle_message_job(
        conn,
        Job(row[0], "whatsapp_message", payload, 1, 5),
        WhatsAppDeps(agent=lambda c: stack.agent, client=client, token="t"),
    )
    by_whatsapp = final_actions(conn, stack)

    # Por web: misma consulta; sin login, se verifica con el código que llega al email.
    session = new_session(api)
    asked = reply(chat(api, session, case["mensaje"]))
    assert "verificar" in asked
    chat(api, session, "lucia@example.com")
    code = re.search(r"\b(\d{6})\b", stack.mailer.sent[-1].body)
    assert code is not None and stack.mailer.sent[-1].to == "lucia@example.com"
    answer = reply(chat(api, session, code.group(1)))
    by_web = final_actions(conn, stack)[len(by_whatsapp) :]

    for keyword in expected["palabras_clave"]:
        assert keyword in sent[0] and keyword in answer, keyword
    consulted = {"tool": "consultar_pedido", "input": {"order_id": "1001"}, "status": "executed"}
    for events in (by_whatsapp, by_web):
        tools = [{k: e[k] for k in consulted} for e in events if "tool" in e]
        assert tools == [consulted]
        assert events[-1]["action"] == expected["accion"]


def test_las_respuestas_llegan_por_server_sent_events(api: TestClient) -> None:
    events = chat(api, new_session(api), "¿Abren los domingos?")
    names = [name for name, _ in events]
    assert names == ["typing", "message", "done"]
    assert "domingos" in events[1][1]["text"]
    assert events[2][1]["remaining"] == MESSAGE_LIMIT - 1


def test_cada_sesion_tiene_un_limite_de_20_mensajes(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    session = new_session(api)
    chat(api, session, "¿Abren los domingos?")
    with tenant_scope(conn, stack.seed.tenant_id):
        conn.execute("update web_sessions set messages = %s", (MESSAGE_LIMIT - 1,))
    last = chat(api, session, "¿Tienen cuotas sin interés?")
    assert last[-1][0] == "done" and last[-1][1]["remaining"] == 0
    with tenant_scope(conn, stack.seed.tenant_id):
        before = conn.execute("select count(*) from case_messages").fetchone()
    blocked = chat(api, session, "¿Y los sábados?")
    assert [name for name, _ in blocked] == ["limit"]
    with tenant_scope(conn, stack.seed.tenant_id):
        after = conn.execute("select count(*) from case_messages").fetchone()
    assert before == after  # el agente no corrió


def test_cada_sesion_es_un_contacto_distinto_sin_datos_en_claro(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    first, second = new_session(api), new_session(api)
    chat(api, first, "¿Abren los domingos?")
    chat(api, second, "¿Abren los domingos?")
    with tenant_scope(conn, stack.seed.tenant_id):
        rows = conn.execute(
            "select c.phone_hash from contacts c join cases k on k.contact_id = c.id"
            " where c.created_at > now() - interval '1 minute'"
        ).fetchall()
        tokens = conn.execute("select token_hash from web_sessions").fetchall()
    assert len({r[0] for r in rows}) == 2
    stored = json.dumps([r[0] for r in rows + tokens])
    assert first not in stored and second not in stored  # el token de sesión no se guarda


@pytest.fixture
def limited(conn: psycopg.Connection, stack: SimulatedStack, address: str) -> Iterator[TestClient]:
    """Chat con límites chicos: 2 mensajes por IP y 3 en total por día (IP del proxy)."""
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn),
        agent=lambda c: stack.agent,
        address=address,
        ip_daily_limit=2,
        daily_limit=3,
        client_ip="x-forwarded-for:first",
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _case_messages(conn: psycopg.Connection, stack: SimulatedStack) -> int:
    with tenant_scope(conn, stack.seed.tenant_id):
        row = conn.execute("select count(*) from case_messages").fetchone()
    assert row is not None
    count: int = row[0]
    return count


def test_cada_ip_tiene_un_limite_diario_aunque_abra_sesiones_nuevas(
    limited: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    first = chat(limited, new_session(limited), "¿Abren los domingos?", ip="203.0.113.5")
    assert first[-1][1]["remaining_today"] == 1
    again = chat(limited, new_session(limited), "¿Abren los domingos?", ip="203.0.113.5")
    assert again[-1][1]["remaining_today"] == 0

    before = _case_messages(conn, stack)
    blocked = chat(limited, new_session(limited), "¿Abren los sábados?", ip="203.0.113.5")
    assert [(name, data["scope"]) for name, data in blocked] == [("limit", "ip")]
    assert _case_messages(conn, stack) == before  # el agente (y Gemini) no corrió

    other = chat(limited, new_session(limited), "¿Abren los domingos?", ip="198.51.100.9")
    assert other[-1][0] == "done"


def test_hay_un_limite_diario_total_para_cuidar_la_cuota_del_modelo(
    limited: TestClient,
) -> None:
    for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        assert chat(limited, new_session(limited), "¿Abren los domingos?", ip=ip)[-1][0] == "done"
    blocked = chat(limited, new_session(limited), "¿Abren los domingos?", ip="203.0.113.4")
    assert [(name, data["scope"]) for name, data in blocked] == [("limit", "daily")]


def test_la_ip_no_se_guarda_en_claro(
    limited: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    chat(limited, new_session(limited), "¿Abren los domingos?", ip="203.0.113.77")
    with tenant_scope(conn, stack.seed.tenant_id):
        rows = conn.execute("select ip_hash from web_usage").fetchall()
    assert len(rows) == 1 and "203.0.113.77" not in rows[0][0]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("direct", "10.1.1.1"),
        ("x-forwarded-for:first", "203.0.113.5"),
        ("x-forwarded-for:last", "10.0.0.7"),
        ("cf-connecting-ip", "198.51.100.2"),
    ],
)
def test_la_ip_del_cliente_sale_del_encabezado_configurado(mode: str, expected: str) -> None:
    headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.7", "cf-connecting-ip": "198.51.100.2"}
    assert client_ip(mode, headers, peer="10.1.1.1") == expected
    assert client_ip(mode, {}, peer="10.1.1.1") == "10.1.1.1"  # sin encabezado: el par TCP


def test_una_sesion_inexistente_se_rechaza(api: TestClient) -> None:
    response = api.post("/chat/messages", json={"session": "x" * 32, "text": "hola"})
    assert response.status_code == 401
    assert api.post("/chat/messages", json={"text": "hola"}).status_code == 401
    assert api.get("/chat/updates", params={"session": "x" * 32}).status_code == 401


def test_la_pagina_recibe_los_mensajes_del_equipo_de_su_sesion(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    """Lo que una persona del equipo escribe desde el panel llega a la sesión del caso, y a
    ninguna otra."""
    mine, other = new_session(api), new_session(api)
    chat(api, mine, "quiero hablar con una persona")
    chat(api, other, "¿Abren los domingos?")
    assert api.get("/chat/updates", params={"session": mine}).json() == {
        "messages": [],
        "typing": False,
    }
    with tenant_scope(conn, stack.seed.tenant_id):
        row = conn.execute("select id from cases where status = 'escalated'").fetchone()
    assert row is not None
    notify_customer(conn, stack.seed.tenant_id, row[0], "Hola, soy Ana del equipo.")

    updates = api.get("/chat/updates", params={"session": mine}).json()["messages"]
    assert [m["text"] for m in updates] == ["Hola, soy Ana del equipo."]
    # Con el id, la página descarta un mensaje que ya mostró (dos consultas superpuestas).
    assert uuid.UUID(updates[0]["id"])
    assert "seen.indexOf(m.id)" in api.get("/widget.js").text
    later = api.get("/chat/updates", params={"session": mine, "after": updates[0]["at"]})
    assert later.json() == {"messages": [], "typing": False}
    assert api.get("/chat/updates", params={"session": other}).json() == {
        "messages": [],
        "typing": False,
    }
    with tenant_scope(conn, stack.seed.tenant_id):
        kinds = [
            r[0]
            for r in conn.execute(
                "select kind from audit_events where case_id = %s order by seq", (row[0],)
            ).fetchall()
        ]
    assert kinds[-1] == "human_reply"  # queda en la auditoría del caso
