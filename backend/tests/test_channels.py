"""Tarea 12e — test de aceptación: dos tenants con números distintos no se mezclan.

El webhook identifica la empresa por `metadata.phone_number_id` (tabla `channels`) y cada
empresa responde con su número y su token, que se guarda cifrado.
"""

import json
import time
import uuid
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb
from simulated.stack import build_simulated_stack

from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.handler import WhatsAppDeps, handle_message_job
from app.channels.whatsapp.routing import channel_for_number, register_whatsapp_channel
from app.contacts import hash_phone
from app.crypto import decrypt
from app.db.connection import tenant_scope
from app.jobs.queue import Job

CUSTOMER = "5491155550000"


def _write(conn: psycopg.Connection, deps: WhatsAppDeps, number: str, text: str) -> None:
    payload = {
        "phone_number_id": number,
        "wamid": f"wamid.{uuid.uuid4().hex}",
        "sender": CUSTOMER,
        "name": "Ana",
        "timestamp": int(time.time()),
        "type": "text",
        "text": text,
    }
    row = conn.execute(
        "insert into jobs (kind, payload) values ('whatsapp_message', %s) returning id",
        (Jsonb(payload),),
    ).fetchone()
    assert row is not None
    handle_message_job(conn, Job(row[0], "whatsapp_message", payload, 1, 5), deps)


def test_dos_tenants_con_numeros_distintos_no_se_mezclan(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    acme = stack.seed.tenant_id
    row = conn.execute(
        "insert into tenants (name, settings) values ('Beta', %s) returning id",
        (Jsonb({"tone": "neutro"}),),
    ).fetchone()
    assert row is not None
    beta: uuid.UUID = row[0]
    register_whatsapp_channel(conn, acme, phone_number_id="111", token="token-acme")
    register_whatsapp_channel(conn, beta, phone_number_id="222", token="token-beta")

    sent: list[dict[str, Any]] = []

    def graph(request: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "url": str(request.url),
                "auth": request.headers["authorization"],
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(200, json={"messages": [{"id": f"wamid.out.{len(sent)}"}]})

    client = WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(graph)))
    deps = WhatsAppDeps(agent=lambda c: stack.agent, client=client, token="token-por-defecto")

    # El mismo cliente le escribe a las dos empresas.
    _write(conn, deps, "111", "¿Abren los domingos?")
    _write(conn, deps, "222", "¿Abren los domingos?")

    assert [s["url"].split("/")[-2] for s in sent] == ["111", "222"]
    assert [s["auth"] for s in sent] == ["Bearer token-acme", "Bearer token-beta"]
    assert "domingos" in sent[0]["body"]["text"]["body"]  # Acme responde con sus documentos
    assert "domingos" not in sent[1]["body"]["text"]["body"]  # Beta no tiene esos datos

    for tenant in (acme, beta):
        with tenant_scope(conn, tenant):
            contacts = conn.execute(
                "select id from contacts where phone_hash = %s", (hash_phone(CUSTOMER),)
            ).fetchall()
            cases = conn.execute(
                "select count(*) from cases where contact_id = %s", (contacts[0][0],)
            ).fetchone()
        assert len(contacts) == 1  # un contacto por empresa, cada uno en su tenant
        assert cases == (1,)  # cada empresa ve solo el caso que le llegó por su número


def test_el_token_del_canal_se_guarda_cifrado(conn: psycopg.Connection) -> None:
    row = conn.execute("insert into tenants (name) values ('Gamma') returning id").fetchone()
    assert row is not None
    register_whatsapp_channel(conn, row[0], phone_number_id="333", token="token-secreto")
    with tenant_scope(conn, row[0]):
        stored = conn.execute(
            "select access_token_enc from channels where phone_number_id = '333'"
        ).fetchone()
    assert stored is not None and "token-secreto" not in stored[0]
    assert decrypt(stored[0]) == "token-secreto"
    channel = channel_for_number(conn, "333")
    assert channel is not None and channel.tenant_id == row[0] and channel.token == "token-secreto"
    assert channel_for_number(conn, "999") is None


def test_un_canal_de_twilio_se_rutea_por_su_tipo(conn: psycopg.Connection) -> None:
    """La prueba de WhatsApp de Twilio identifica a la empresa por su número de envío."""
    row = conn.execute("insert into tenants (name) values ('Delta') returning id").fetchone()
    assert row is not None
    address = "whatsapp:+15550001111"
    register_whatsapp_channel(conn, row[0], phone_number_id=address, kind="twilio_whatsapp")
    channel = channel_for_number(conn, address, kind="twilio_whatsapp")
    assert channel is not None and channel.tenant_id == row[0]
    assert channel.token is None  # las credenciales de Twilio vienen del entorno
    assert channel_for_number(conn, address) is None  # no es un número de la Cloud API de Meta
