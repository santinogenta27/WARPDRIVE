"""Hito QA 2 — test de aceptación: sin modelo disponible, se deriva (decisión 098).

En la etapa 3 se agotaron en el mismo día las cuotas gratuitas de Groq y de Gemini. En ese
momento el chat le mostraba al cliente "Tuvimos un problema para responderte", la transacción se
revertía y el mensaje se perdía: nadie del equipo se enteraba.
"""

import json
from contextlib import nullcontext

import psycopg
from fastapi.testclient import TestClient
from simulated.stack import ExhaustedProvider, build_simulated_stack

from app import i18n
from app.agent.loop import Agent
from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.db.connection import tenant_scope
from app.llm.client import LLMClient, PostgresUsageRecorder
from app.main import app


def test_sin_cuota_el_caso_se_deriva_y_no_se_pierde(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    llm = LLMClient(provider=ExhaustedProvider(), recorder=PostgresUsageRecorder(conn))
    agent = Agent(conn, llm, stack.embedder, stack.agent._guardian, mailer=stack.mailer)
    contact = stack.seed.contacts["lucia"]
    result = agent.handle_message(stack.seed.tenant_id, contact, "¿Abren los domingos?")
    assert result.action == "derivar"
    assert result.message.startswith(i18n.text("es", "escalation"))
    with tenant_scope(conn, stack.seed.tenant_id):
        status = conn.execute("select status from cases where id = %s", (result.case_id,))
        assert status.fetchone() == ("escalated",)
        cause = conn.execute(
            "select payload -> 'causa' ->> 'tipo' from audit_events where case_id = %s"
            " and kind = 'escalated'",
            (result.case_id,),
        ).fetchone()
        texts = [r[0] for r in conn.execute(
            "select text from case_messages where case_id = %s and role = 'customer'",
            (result.case_id,),
        ).fetchall()]  # fmt: skip
    assert cause == ("modelo_no_disponible",)
    assert texts == ["¿Abren los domingos?"]


def test_el_chat_web_no_muestra_un_error(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    llm = LLMClient(provider=ExhaustedProvider(), recorder=PostgresUsageRecorder(conn))
    agent = Agent(conn, llm, stack.embedder, stack.agent._guardian, mailer=stack.mailer)
    address = "web:qa-sin-modelo"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: agent, address=address
    )
    try:
        api = TestClient(app)
        session = api.post("/chat/session").json()["session"]
        answer = api.post(
            "/chat/messages", json={"session": session, "text": "Hola, ¿hacen envíos?"}
        )
    finally:
        app.dependency_overrides.clear()
    events = []
    for block in answer.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    names = [name for name, _ in events]
    assert "error" not in names, events
    said = " ".join(d["text"] for name, d in events if name == "message")
    assert i18n.text("es", "escalation") in said
