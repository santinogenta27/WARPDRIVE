"""Procesa un mensaje de WhatsApp encolado por el webhook: corre el agente y responde.

Corre dentro de la transacción del worker (app/jobs/worker.py): si el envío falla, se revierte
todo y el job se reintenta.
"""

import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from app.agent.loop import Agent
from app.audit import log as audit_log
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.webhook import JOB_KIND, InboundMessage
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.jobs.queue import Job

__all__ = ["JOB_KIND", "WhatsAppDeps", "handle_message_job", "production_deps"]

logger = logging.getLogger(__name__)
IGNORED_TYPES = {"sticker", "reaction", "unsupported", "system"}
UNSUPPORTED_MESSAGE = "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribís?"


@dataclass
class WhatsAppDeps:
    agent: Callable[[psycopg.Connection], Agent]
    client: WhatsAppClient
    token: str


def tenant_for_number(conn: psycopg.Connection, phone_number_id: str) -> uuid.UUID | None:
    """Empresa dueña del número. Única consulta fuera de un tenant: es el ruteo del webhook."""
    row = conn.execute(
        "select id from tenants where settings -> 'whatsapp' ->> 'phone_number_id' = %s",
        (phone_number_id,),
    ).fetchone()
    return row[0] if row else None


def handle_message_job(conn: psycopg.Connection, job: Job, deps: WhatsAppDeps) -> None:
    message = InboundMessage(**job.payload)
    tenant_id = tenant_for_number(conn, message.phone_number_id)
    if tenant_id is None:
        logger.warning(
            "Mensaje para un número sin empresa (%s): se descarta", message.phone_number_id
        )
        _forget_sender(conn, job)
        return
    contact_id = _touch_contact(conn, tenant_id, message)
    if message.type in IGNORED_TYPES:
        _forget_sender(conn, job)
        return
    case_id: uuid.UUID | None = None
    if message.type == "text" and message.text.strip():
        result = deps.agent(conn).handle_message(tenant_id, contact_id, message.text)
        reply, case_id = result.message, result.case_id
    else:
        reply = UNSUPPORTED_MESSAGE
    wamid = deps.client.send_text(
        token=deps.token, phone_number_id=message.phone_number_id, to=message.sender, body=reply
    )
    if case_id is not None:
        audit_log.record(
            conn,
            tenant_id,
            case_id,
            "whatsapp_sent",
            {"wamid": wamid, "in_reply_to": message.wamid},
        )
    _forget_sender(conn, job)


def _touch_contact(
    conn: psycopg.Connection, tenant_id: uuid.UUID, message: InboundMessage
) -> uuid.UUID:
    """Crea el contacto si no existe y registra la hora del último mensaje del cliente."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into contacts (tenant_id, phone_hash, last_customer_message_at)"
            " values (%s, %s, to_timestamp(%s))"
            " on conflict (tenant_id, phone_hash) do update set last_customer_message_at ="
            " greatest(contacts.last_customer_message_at, excluded.last_customer_message_at)"
            " returning id",
            (tenant_id, hash_phone(message.sender), message.timestamp),
        ).fetchone()
    assert row is not None
    contact_id: uuid.UUID = row[0]
    return contact_id


def _forget_sender(conn: psycopg.Connection, job: Job) -> None:
    """El teléfono en claro solo hace falta para responder: después se borra del job."""
    conn.execute("update jobs set payload = payload - 'sender' where id = %s", (job.id,))


def production_deps() -> WhatsAppDeps:
    """Agente con los modelos del entorno y la API de Acme simulada en proceso (sandbox)."""
    from app.demo.acme import acme_sandbox_client
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.llm.factory import embedder_from_env, provider_from_env
    from app.policy.guardian import Guardian
    from app.tools.runtime import ToolRuntime

    agents: dict[int, Agent] = {}
    acme = acme_sandbox_client()

    def agent(conn: psycopg.Connection) -> Agent:
        if id(conn) not in agents:
            recorder = PostgresUsageRecorder(conn)
            llm = LLMClient(provider=provider_from_env(), recorder=recorder)
            guardian = Guardian(conn, ToolRuntime(acme))
            agents[id(conn)] = Agent(conn, llm, embedder_from_env(recorder), guardian)
        return agents[id(conn)]

    return WhatsAppDeps(
        agent=agent, client=WhatsAppClient(), token=os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    )
