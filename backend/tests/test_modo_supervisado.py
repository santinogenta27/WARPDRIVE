"""Hito 11, tarea 1 — test de aceptación del modo supervisado (decisión 072).

Con el modo activo, cada respuesta del agente queda como borrador en la bandeja. Una persona la
envía (tal cual o editada) o la rechaza antes de que llegue al cliente. Mientras tanto, el cliente
ve "escribiendo…". Las herramientas siguen pasando por el guardián, y todo queda en la auditoría.
"""

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack
from test_panel_ingles import spanish_left

from app import i18n
from app.cases import supervision
from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.handler import JOB_KIND, WhatsAppDeps, handle_message_job
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.db.connection import tenant_scope
from app.jobs.queue import Job, enqueue
from app.main import app
from app.panel import auth, english
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
WA_NUMBER = "106540352242977"


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    address: str
    delivered: list[tuple[str, str]] = field(default_factory=list)  # (canal, texto)

    @property
    def tenant(self) -> uuid.UUID:
        return self.stack.seed.tenant_id


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    address = f"web:test-{uuid.uuid4().hex}"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    found = World(conn, stack, address)

    def deliver(
        c: psycopg.Connection, tenant: uuid.UUID, case: uuid.UUID, channel: str, text: str
    ) -> None:
        found.delivered.append((channel, text))

    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent, address=address
    )
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        deliver=deliver,
    )
    try:
        yield found
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def panel() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def csrf(markup: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', markup)
    assert found is not None
    return found.group(1)


def chat(api: TestClient, session: str, text: str) -> list[tuple[str, dict[str, Any]]]:
    response = api.post("/chat/messages", json={"session": session, "text": text})
    assert response.status_code == 200, response.text
    events = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def updates(api: TestClient, session: str) -> dict[str, Any]:
    response = api.get("/chat/updates", params={"session": session})
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def kinds(world: World) -> list[str]:
    with tenant_scope(world.conn, world.tenant):
        rows = world.conn.execute(
            "select kind from audit_events where kind like 'draft_%%' order by seq"
        ).fetchall()
    return [r[0] for r in rows]


def held_drafts(world: World) -> list[supervision.Draft]:
    return supervision.pending(world.conn, world.tenant)


def test_apagado_por_defecto_la_respuesta_llega_directo(world: World) -> None:
    api = TestClient(app)
    session = api.post("/chat/session").json()["session"]
    events = chat(api, session, "Hola")
    assert [name for name, _ in events if name == "message"] == ["message"]
    assert held_drafts(world) == []


def test_la_respuesta_queda_como_borrador_y_el_cliente_ve_escribiendo(world: World) -> None:
    supervision.set_enabled(world.conn, world.tenant, True)
    api = TestClient(app)
    session = api.post("/chat/session").json()["session"]

    events = chat(api, session, "Hola")
    names = [name for name, _ in events]
    assert "message" not in names and "held" in names and names[-1] == "done"
    assert updates(api, session) == {"messages": [], "typing": True}

    drafts = held_drafts(world)
    assert len(drafts) == 1
    draft = drafts[0]
    # El borrador no está en el historial del caso: el modelo no lo ve en el turno siguiente.
    with tenant_scope(world.conn, world.tenant):
        roles = world.conn.execute(
            "select role from case_messages where case_id = %s order by created_at",
            (draft.case_id,),
        ).fetchall()
    assert [r[0] for r in roles] == ["customer"]

    # En la bandeja, con el texto editable y los botones.
    client = panel()
    inbox = client.get("/panel/inbox/drafts").text
    assert "Borradores por revisar" in inbox
    assert f'action="/panel/borradores/{draft.id}"' in inbox
    assert draft.text.split("\n")[0] in inbox
    detail = client.get(f"/panel/casos/{draft.case_id}").text
    assert f'action="/panel/borradores/{draft.id}"' in detail

    edited = "¡Hola! Soy del equipo de Acme, ¿en qué te ayudo?"
    sent = client.post(
        f"/panel/borradores/{draft.id}",
        data={"csrf": csrf(inbox), "decision": "enviar", "texto": edited, "volver": "/panel/inbox"},
        follow_redirects=False,
    )
    assert sent.status_code == 303 and "aviso=borrador-enviado" in sent.headers["location"]

    data = updates(api, session)
    assert data["typing"] is False
    assert [(m["text"], m["role"]) for m in data["messages"]] == [(edited, "agent")]
    assert world.delivered == [("web", edited)]
    assert held_drafts(world) == []
    assert kinds(world) == ["draft_held", "draft_sent"]
    with tenant_scope(world.conn, world.tenant):
        payload = world.conn.execute(
            "select payload from audit_events where kind = 'draft_sent'"
        ).fetchone()
    assert payload is not None
    assert payload[0]["edited"] is True and payload[0]["text"] == edited

    # Un segundo envío del mismo borrador (doble clic) no manda nada de nuevo.
    again = client.post(
        f"/panel/borradores/{draft.id}",
        data={"csrf": csrf(inbox), "decision": "enviar", "texto": edited},
        follow_redirects=False,
    )
    assert again.status_code == 303
    assert world.delivered == [("web", edited)]


def test_rechazar_no_envia_nada(world: World) -> None:
    supervision.set_enabled(world.conn, world.tenant, True)
    api = TestClient(app)
    session = api.post("/chat/session").json()["session"]
    chat(api, session, "Hola")
    draft = held_drafts(world)[0]

    client = panel()
    token = csrf(client.get("/panel/inbox").text)
    client.post(
        f"/panel/borradores/{draft.id}",
        data={"csrf": token, "decision": "rechazar", "texto": draft.text},
        follow_redirects=False,
    )
    assert updates(api, session) == {"messages": [], "typing": False}
    assert world.delivered == []
    assert kinds(world) == ["draft_held", "draft_rejected"]


def test_sin_editar_queda_registrado_como_no_editado(world: World) -> None:
    supervision.set_enabled(world.conn, world.tenant, True)
    api = TestClient(app)
    session = api.post("/chat/session").json()["session"]
    chat(api, session, "Hola")
    draft = held_drafts(world)[0]
    client = panel()
    token = csrf(client.get("/panel/inbox").text)
    client.post(
        f"/panel/borradores/{draft.id}",
        data={"csrf": token, "decision": "enviar", "texto": draft.text},
    )
    with tenant_scope(world.conn, world.tenant):
        payload = world.conn.execute(
            "select payload from audit_events where kind = 'draft_sent'"
        ).fetchone()
    assert payload is not None and payload[0]["edited"] is False


def test_las_herramientas_siguen_pasando_por_el_guardian(world: World) -> None:
    """El agente corre completo antes del borrador: clasificación, guardián y auditoría."""
    supervision.set_enabled(world.conn, world.tenant, True)
    api = TestClient(app)
    session = api.post("/chat/session").json()["session"]
    chat(api, session, "Quiero el reembolso del pedido A-1001")
    draft = held_drafts(world)[0]
    with tenant_scope(world.conn, world.tenant):
        rows = world.conn.execute(
            "select kind from audit_events where case_id = %s order by seq", (draft.case_id,)
        ).fetchall()
    order = [r[0] for r in rows]
    assert "classified" in order and "final_action" in order
    assert order.index("final_action") < order.index("draft_held")


def test_se_activa_desde_configuracion(world: World) -> None:
    client = panel()
    page = client.get("/panel/agent").text
    assert "Revisar antes de enviar" in page
    saved = client.post(
        "/panel/configuracion/supervisado", data={"csrf": csrf(page), "activo": "si"}
    )
    assert "Modo supervisado activado." in saved.text
    assert supervision.enabled(world.conn, world.tenant)
    client.post("/panel/configuracion/supervisado", data={"csrf": csrf(page), "activo": "no"})
    assert not supervision.enabled(world.conn, world.tenant)


class FakeGraph:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def client(self) -> WhatsAppClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"messages": [{"id": "wamid.out"}], "success": True})

        return WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_por_whatsapp_queda_el_borrador_y_se_muestra_escribiendo(world: World) -> None:
    register_whatsapp_channel(world.conn, world.tenant, phone_number_id=WA_NUMBER, token="t")
    supervision.set_enabled(world.conn, world.tenant, True)
    graph = FakeGraph()
    deps = WhatsAppDeps(agent=lambda c: world.stack.agent, client=graph.client(), token="t")
    payload = {
        "phone_number_id": WA_NUMBER,
        "wamid": "wamid.entrante.1",
        "sender": "5491155550077",
        "timestamp": 1762700000,
        "type": "text",
        "text": "Hola",
        "name": "Ana",
    }
    job_id = enqueue(world.conn, JOB_KIND, payload)
    job = Job(id=job_id, kind=JOB_KIND, payload=payload, attempts=1, max_attempts=5)
    handle_message_job(world.conn, job, deps)

    # Ningún texto al cliente: solo el indicador de "escribiendo" sobre su mensaje.
    assert [m.get("type") for m in graph.sent] == [None]
    assert graph.sent[0]["typing_indicator"] == {"type": "text"}
    assert graph.sent[0]["message_id"] == "wamid.entrante.1"
    drafts = held_drafts(world)
    assert len(drafts) == 1 and drafts[0].channel == "whatsapp"

    client = panel()
    token = csrf(client.get("/panel/inbox").text)
    client.post(
        f"/panel/borradores/{drafts[0].id}",
        data={"csrf": token, "decision": "enviar", "texto": drafts[0].text},
    )
    assert world.delivered == [("whatsapp", drafts[0].text)]


def _new_spanish(markup: str) -> list[str]:
    """El pie de la demo de Acme (en español a propósito) no es de esta tarea."""
    return [t for t in spanish_left(markup) if "Acme Store" not in t]


def test_la_bandeja_con_borradores_en_ingles(world: World) -> None:
    i18n.set_language(world.conn, world.tenant, "en")
    supervision.set_enabled(world.conn, world.tenant, True)
    api = TestClient(app)
    session = api.post("/chat/session").json()["session"]
    chat(api, session, "Hi")
    draft = held_drafts(world)[0]
    client = panel()
    inbox = client.get("/panel/inbox/drafts").text
    assert "Drafts to review" in inbox and _new_spanish(inbox) == []
    detail = client.get(f"/panel/casos/{draft.case_id}").text
    assert _new_spanish(detail) == []
    assert "Supervised mode" in client.get("/panel/agent").text
