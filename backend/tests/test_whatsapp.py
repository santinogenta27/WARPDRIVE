"""Tarea 12 — test de aceptación: un mensaje al número de prueba recibe respuesta.

Recorre el camino completo sin red: Meta llama al webhook (con firma), el webhook encola el
mensaje, el worker corre el agente real (con Claude simulado) y responde por la Cloud API
(transporte simulado). La prueba con el número real de Meta se hace a mano (docs/whatsapp.md).
"""

import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.handler import JOB_KIND, WhatsAppDeps, handle_message_job
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.channels.whatsapp.webhook import (
    ConnectionStore,
    WebhookSettings,
    message_store,
    webhook_settings,
)
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.jobs.queue import Job
from app.main import app

SECRET = "secreto-de-prueba"
VERIFY = "token-de-verificacion"
PHONE_NUMBER_ID = "106540352242922"
CUSTOMER = "5491155550000"


def inbound(wamid: str, text: str, *, phone_number_id: str = PHONE_NUMBER_ID) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"profile": {"name": "Ana"}, "wa_id": CUSTOMER}],
                            "messages": [
                                {
                                    "from": CUSTOMER,
                                    "id": wamid,
                                    "timestamp": "1762700000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeGraph:
    """Cloud API de Meta simulada: guarda lo que se envía."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def client(self) -> WhatsAppClient:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer token-meta"
            self.sent.append({"url": str(request.url), "body": json.loads(request.content)})
            return httpx.Response(
                200, json={"messages": [{"id": f"wamid.saliente.{len(self.sent)}"}]}
            )

        return WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    stack = build_simulated_stack(conn)
    register_whatsapp_channel(
        conn, stack.seed.tenant_id, phone_number_id=PHONE_NUMBER_ID, token="token-meta"
    )
    return stack


@pytest.fixture
def api(conn: psycopg.Connection) -> Iterator[TestClient]:
    # La transacción del test se revierte al final: el webhook no confirma nada.
    app.dependency_overrides[message_store] = lambda: ConnectionStore(conn, commit=False)
    app.dependency_overrides[webhook_settings] = lambda: WebhookSettings(SECRET, VERIFY)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def post(api: TestClient, payload: dict[str, Any], signature: str | None = None) -> int:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": signature or sign(body)}
    return api.post("/webhooks/whatsapp", content=body, headers=headers).status_code


def queued(conn: psycopg.Connection) -> list[Job]:
    rows = conn.execute(
        "select id, kind, payload, attempts, max_attempts from jobs where kind = %s"
        " and status = 'queued' order by created_at",
        (JOB_KIND,),
    ).fetchall()
    return [Job(id=r[0], kind=r[1], payload=r[2], attempts=r[3], max_attempts=r[4]) for r in rows]


def test_handshake_de_verificacion(api: TestClient) -> None:
    ok = api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


def test_firma_invalida_se_rechaza_y_no_encola(api: TestClient, conn: psycopg.Connection) -> None:
    assert post(api, inbound("wamid.A", "hola"), signature="sha256=" + "0" * 64) == 401
    body = json.dumps(inbound("wamid.A", "hola")).encode()
    assert post(api, inbound("wamid.A", "hola"), signature=sign(body, "otro-secreto")) == 401
    assert queued(conn) == []


def test_un_mensaje_al_numero_de_prueba_recibe_respuesta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    payload = inbound("wamid.HBgLMTY1MDM4", "¿Abren los domingos?")
    assert post(api, payload) == 200
    assert post(api, payload) == 200  # Meta reenvía el mismo webhook: no se duplica
    jobs = queued(conn)
    assert len(jobs) == 1

    graph = FakeGraph()
    deps = WhatsAppDeps(agent=lambda c: stack.agent, client=graph.client(), token="token-meta")
    handle_message_job(conn, jobs[0], deps)

    assert len(graph.sent) == 1
    sent = graph.sent[0]
    assert sent["url"] == f"https://graph.facebook.com/v27.0/{PHONE_NUMBER_ID}/messages"
    assert sent["body"]["to"] == CUSTOMER and sent["body"]["type"] == "text"
    assert "domingos" in sent["body"]["text"]["body"]

    # El contacto queda en el tenant del número, con el teléfono solo como hash.
    with tenant_scope(conn, stack.seed.tenant_id):
        contact = conn.execute(
            "select id, last_customer_message_at from contacts where phone_hash = %s",
            (hash_phone(CUSTOMER),),
        ).fetchone()
        assert contact is not None and contact[1] is not None
        kinds = [
            r[0]
            for r in conn.execute(
                "select e.kind from audit_events e join cases c on c.id = e.case_id"
                " where c.contact_id = %s order by e.seq",
                (contact[0],),
            ).fetchall()
        ]
    assert kinds[0] == "message_received" and kinds[-1] == "whatsapp_sent"
    # El teléfono en claro no queda guardado en el job una vez respondido.
    payload_after = conn.execute("select payload from jobs where id = %s", (jobs[0].id,)).fetchone()
    assert payload_after is not None and CUSTOMER not in json.dumps(payload_after[0])


def test_estados_y_stickers_no_generan_respuesta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    status = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                            "statuses": [{"id": "wamid.X", "status": "delivered"}],
                        },
                    }
                ]
            }
        ],
    }
    assert post(api, status) == 200
    assert queued(conn) == []

    sticker = inbound("wamid.S", "")
    message = sticker["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = "sticker"
    del message["text"]
    assert post(api, sticker) == 200
    graph = FakeGraph()
    deps = WhatsAppDeps(agent=lambda c: stack.agent, client=graph.client(), token="token-meta")
    for job in queued(conn):
        handle_message_job(conn, job, deps)
    assert graph.sent == []


def test_celular_argentino_fuera_de_la_lista_se_reintenta_sin_el_9() -> None:
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        to = json.loads(request.content)["to"]
        tried.append(to)
        if to.startswith("549"):
            error = {"code": 131030, "message": "Recipient phone number not in allowed list"}
            return httpx.Response(400, json={"error": error})
        return httpx.Response(200, json={"messages": [{"id": "wamid.ok"}]})

    client = WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(handler)))
    wamid = client.send_text(token="t", phone_number_id="1", to="5491155550000", body="hola")
    assert wamid == "wamid.ok" and tried == ["5491155550000", "541155550000"]


def test_si_el_numero_no_esta_permitido_no_se_reintenta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    """131030 no se arregla reintentando: solo volvería a correr el agente y gastaría cuota."""
    attempts: list[str] = []

    def rejects(request: httpx.Request) -> httpx.Response:
        attempts.append(json.loads(request.content)["to"])
        error = {"code": 131030, "message": "Recipient phone number not in allowed list"}
        return httpx.Response(400, json={"error": error})

    client = WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(rejects)))
    deps = WhatsAppDeps(agent=lambda c: stack.agent, client=client, token="token-meta")
    assert post(api, inbound("wamid.N", "¿Abren los domingos?")) == 200
    job = queued(conn)[0]

    handle_message_job(conn, job, deps)  # no lanza: el worker no reprograma el job

    assert attempts == [CUSTOMER, "54" + CUSTOMER[3:]]  # con y sin el 9, una vez cada uno
    with tenant_scope(conn, stack.seed.tenant_id):
        failed = conn.execute(
            "select e.payload from audit_events e where e.kind = 'whatsapp_send_failed'"
        ).fetchone()
    assert failed is not None and failed[0]["code"] == 131030
    payload = conn.execute("select payload from jobs where id = %s", (job.id,)).fetchone()
    assert payload is not None and "sender" not in payload[0]


def test_un_envio_que_meta_no_entrega_queda_en_el_log(
    api: TestClient, conn: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Meta acepta el envío (200) y avisa después, por webhook, si no lo pudo entregar."""
    failed = {
        "id": "wamid.saliente.1",
        "status": "failed",
        "timestamp": "1762700100",
        "recipient_id": CUSTOMER,
        "errors": [{"code": 131026, "title": "Message undeliverable"}],
    }
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                            "statuses": [failed, {"id": "wamid.saliente.2", "status": "read"}],
                        },
                    }
                ]
            }
        ],
    }
    with caplog.at_level("WARNING", logger="app.channels.whatsapp.webhook"):
        assert post(api, payload) == 200
    assert queued(conn) == []
    logged = [r.getMessage() for r in caplog.records]
    assert len(logged) == 1
    assert "131026" in logged[0] and "wamid.saliente.1" in logged[0]
    assert CUSTOMER not in logged[0]  # el teléfono no va al log


def test_un_numero_desconocido_se_descarta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    assert post(api, inbound("wamid.D", "hola", phone_number_id="999")) == 200
    graph = FakeGraph()
    deps = WhatsAppDeps(agent=lambda c: stack.agent, client=graph.client(), token="token-meta")
    for job in queued(conn):
        handle_message_job(conn, job, deps)
    assert graph.sent == []
    with tenant_scope(conn, stack.seed.tenant_id):  # el tenant de Acme no recibió nada
        found = conn.execute(
            "select 1 from contacts where phone_hash = %s", (hash_phone(CUSTOMER),)
        ).fetchone()
    assert found is None
