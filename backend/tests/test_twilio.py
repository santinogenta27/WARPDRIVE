"""Canal twilio_whatsapp: un mensaje por la prueba de WhatsApp de Twilio recibe respuesta.

Mismo camino que la Cloud API de Meta (test_whatsapp.py), con otro transporte: Twilio llama al
webhook con un formulario firmado (X-Twilio-Signature), el webhook encola el mensaje y el worker
corre el mismo agente, guardián y auditoría con el tenant del número, y responde por la API de
Twilio (simulada). La prueba con el sandbox real se hace a mano (docs/whatsapp.md).
"""

import base64
import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.base import Channel
from app.channels.inbound import handle_inbound_job
from app.channels.twilio.channel import JOB_KIND, TwilioWhatsApp
from app.channels.twilio.webhook import twilio_channel, twilio_store
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.handler import WhatsAppDeps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.channels.whatsapp.webhook import ConnectionStore
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.jobs.__main__ import build_handlers
from app.jobs.queue import Job
from app.main import app

SID = "AC00000000000000000000000000000000"
AUTH = "auth-token-de-prueba"
FROM = "whatsapp:+14155238886"
CUSTOMER = "5491155550000"
URL = "http://testserver/webhooks/twilio"


def form(message_sid: str, text: str, **extra: str) -> dict[str, str]:
    return {
        "SmsMessageSid": message_sid,
        "MessageSid": message_sid,
        "AccountSid": SID,
        "From": f"whatsapp:+{CUSTOMER}",
        "To": FROM,
        "WaId": CUSTOMER,
        "ProfileName": "Ana",
        "Body": text,
        "NumMedia": "0",
        "NumSegments": "1",
        "MessageType": "text",
        "ApiVersion": "2010-04-01",
        **extra,
    }


def sign(params: dict[str, str], token: str = AUTH, url: str = URL) -> str:
    """Firma de Twilio: HMAC-SHA1 de la URL más cada parámetro (ordenados) en base64."""
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


class FakeTwilio:
    """API de mensajes de Twilio simulada: guarda lo que se envía."""

    def __init__(self, status: int = 201, error_code: int | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.status = status
        self.error_code = error_code

    def channel(self) -> TwilioWhatsApp:
        def handler(request: httpx.Request) -> httpx.Response:
            expected = "Basic " + base64.b64encode(f"{SID}:{AUTH}".encode()).decode()
            assert request.headers["authorization"] == expected
            body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.sent.append({"url": str(request.url), "body": body})
            if self.status >= 400:
                return httpx.Response(
                    self.status, json={"code": self.error_code, "message": "rechazado"}
                )
            return httpx.Response(201, json={"sid": f"SM{len(self.sent):032d}"})

        return TwilioWhatsApp(
            account_sid=SID,
            auth_token=AUTH,
            from_address=FROM,
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    stack = build_simulated_stack(conn)
    register_whatsapp_channel(
        conn, stack.seed.tenant_id, phone_number_id=FROM, kind="twilio_whatsapp"
    )
    return stack


@pytest.fixture
def api(conn: psycopg.Connection) -> Iterator[TestClient]:
    app.dependency_overrides[twilio_store] = lambda: ConnectionStore(
        conn, commit=False, kind=JOB_KIND, key_prefix="twilio"
    )
    app.dependency_overrides[twilio_channel] = lambda: FakeTwilio().channel()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def post(api: TestClient, params: dict[str, str], signature: str | None = None) -> httpx.Response:
    return api.post(
        "/webhooks/twilio",
        content=urlencode(params),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature if signature is not None else sign(params),
        },
    )


def queued(conn: psycopg.Connection) -> list[Job]:
    rows = conn.execute(
        "select id, kind, payload, attempts, max_attempts from jobs where kind = %s"
        " and status = 'queued' order by created_at",
        (JOB_KIND,),
    ).fetchall()
    return [Job(id=r[0], kind=r[1], payload=r[2], attempts=r[3], max_attempts=r[4]) for r in rows]


def test_twilio_cumple_la_interfaz_channel() -> None:
    channel: Channel = FakeTwilio().channel()
    assert channel.kind == "twilio_whatsapp"


def test_firma_invalida_se_rechaza_y_no_encola(api: TestClient, conn: psycopg.Connection) -> None:
    params = form("SM1", "hola")
    assert post(api, params, signature="").status_code == 401
    assert post(api, params, signature=sign(params, token="otro-token")).status_code == 401
    tampered = sign(params)
    assert post(api, {**params, "Body": "otra cosa"}, signature=tampered).status_code == 401
    assert queued(conn) == []


def test_un_mensaje_por_twilio_recibe_respuesta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    params = form("SMa1b2c3", "¿Abren los domingos?")
    first = post(api, params)
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/xml")  # TwiML vacío: no responde ahí
    assert post(api, params).status_code == 200  # Twilio reintenta: no se duplica
    jobs = queued(conn)
    assert len(jobs) == 1

    twilio = FakeTwilio()
    handle_inbound_job(conn, jobs[0], channel=twilio.channel(), agent=lambda c: stack.agent)

    assert len(twilio.sent) == 1
    sent = twilio.sent[0]
    assert sent["url"] == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
    assert sent["body"]["From"] == FROM
    assert sent["body"]["To"] == f"whatsapp:+{CUSTOMER}"
    assert "domingos" in sent["body"]["Body"]

    # Mismo contacto que por Meta (hash del wa_id), en el tenant del número, con auditoría.
    with tenant_scope(conn, stack.seed.tenant_id):
        contact = conn.execute(
            "select id, last_customer_message_at from contacts where phone_hash = %s",
            (hash_phone(CUSTOMER),),
        ).fetchone()
        assert contact is not None and contact[1] is not None
        events = conn.execute(
            "select e.kind, e.payload from audit_events e join cases c on c.id = e.case_id"
            " where c.contact_id = %s order by e.seq",
            (contact[0],),
        ).fetchall()
    assert events[0][0] == "message_received" and events[-1][0] == "whatsapp_sent"
    assert events[-1][1]["channel"] == "twilio_whatsapp"
    payload_after = conn.execute("select payload from jobs where id = %s", (jobs[0].id,)).fetchone()
    assert payload_after is not None and CUSTOMER not in json.dumps(payload_after[0])


def test_si_twilio_rechaza_el_envio_no_se_reintenta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    """Un 4xx de Twilio (por ejemplo, el cliente salió del sandbox) no se arregla reintentando."""
    assert post(api, form("SMd4e5", "¿Abren los domingos?")).status_code == 200
    job = queued(conn)[0]
    twilio = FakeTwilio(status=400, error_code=63015)

    handle_inbound_job(conn, job, channel=twilio.channel(), agent=lambda c: stack.agent)

    assert len(twilio.sent) == 1
    with tenant_scope(conn, stack.seed.tenant_id):
        failed = conn.execute(
            "select payload from audit_events where kind = 'whatsapp_send_failed'"
        ).fetchone()
    assert failed is not None and failed[0]["code"] == 63015


def test_el_worker_atiende_twilio_solo_si_esta_configurado() -> None:
    meta = WhatsAppDeps(agent=lambda c: None, client=WhatsAppClient(), token="")  # type: ignore[arg-type,return-value]
    with_twilio = build_handlers(meta, FakeTwilio().channel())
    assert set(with_twilio) == {"whatsapp_message", JOB_KIND}
    assert set(build_handlers(meta, None)) == {"whatsapp_message"}


def test_un_numero_de_twilio_desconocido_se_descarta(
    api: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    params = form("SMf6", "hola", To="whatsapp:+19999999999")
    assert post(api, params).status_code == 200
    twilio = FakeTwilio()
    for job in queued(conn):
        handle_inbound_job(conn, job, channel=twilio.channel(), agent=lambda c: stack.agent)
    assert twilio.sent == []
