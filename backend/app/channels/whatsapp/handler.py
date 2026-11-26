"""Procesa un mensaje de WhatsApp encolado por el webhook: corre el agente y responde.

Corre dentro de la transacción del worker (app/jobs/worker.py): si el envío falla, se revierte
todo y el job se reintenta.
"""

import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import psycopg

from app.agent.loop import Agent
from app.audit import log as audit_log
from app.cases import supervision
from app.channels import links
from app.channels.base import (
    IGNORED_TYPES,
    UNSUPPORTED_MESSAGE,
    InboundMessage,
    forget_sender,
    touch_contact,
)
from app.channels.whatsapp.client import WhatsAppClient, WhatsAppError
from app.channels.whatsapp.outbound import flush_pending
from app.channels.whatsapp.routing import channel_for_number
from app.channels.whatsapp.webhook import JOB_KIND
from app.jobs.queue import Job

__all__ = ["JOB_KIND", "WhatsAppDeps", "handle_message_job", "production_deps"]

logger = logging.getLogger(__name__)
# Rechazos de Meta que no se arreglan reintentando: número fuera de la lista permitida del
# número de prueba (131030) y mensaje imposible de entregar (131026).
PERMANENT_SEND_ERRORS = {131030, 131026}


@dataclass
class WhatsAppDeps:
    agent: Callable[[psycopg.Connection], Agent]
    client: WhatsAppClient
    token: str  # token por defecto (WHATSAPP_ACCESS_TOKEN) si el canal no tiene uno guardado


def handle_message_job(conn: psycopg.Connection, job: Job, deps: WhatsAppDeps) -> None:
    message = InboundMessage(**job.payload)
    channel = channel_for_number(conn, message.phone_number_id)
    if channel is None:  # número desconocido: se registra en el log y se descarta (spec)
        logger.warning(
            "Mensaje para un número sin empresa (%s): se descarta", message.phone_number_id
        )
        forget_sender(conn, job)
        return
    tenant_id = channel.tenant_id
    token = channel.token or deps.token
    contact_id = touch_contact(conn, tenant_id, message)
    if message.type in IGNORED_TYPES:
        forget_sender(conn, job)
        return
    # Si había mensajes esperando que se abra la ventana (el cliente respondió la plantilla),
    # se envían y este mensaje no pasa por el agente.
    if flush_pending(
        conn,
        tenant_id,
        contact_id,
        phone_number_id=message.phone_number_id,
        to=message.sender,
        client=deps.client,
        token=token,
    ):
        forget_sender(conn, job)
        return
    case_id: uuid.UUID | None = None
    if message.type == "text" and message.text.strip():
        result = deps.agent(conn).handle_message(tenant_id, contact_id, message.text)
        reply, case_id = result.message, result.case_id
    else:
        reply = UNSUPPORTED_MESSAGE
    if case_id is not None and supervision.enabled(conn, tenant_id):
        # Modo supervisado (decisión 072): la respuesta espera a una persona del equipo.
        supervision.hold(conn, tenant_id, case_id, reply, channel="whatsapp")
        try:
            deps.client.typing(
                token=token, phone_number_id=message.phone_number_id, message_id=message.wamid
            )
        except (WhatsAppError, httpx.HTTPError) as error:  # solo un indicador: no se reintenta
            logger.warning("WhatsApp no mostró el indicador de escribiendo: %s", error)
        forget_sender(conn, job)
        return
    reply = links.for_whatsapp(conn, tenant_id, reply)  # cada link en su línea, corto
    try:
        wamid = deps.client.send_text(
            token=token, phone_number_id=message.phone_number_id, to=message.sender, body=reply
        )
    except WhatsAppError as error:
        if error.code not in PERMANENT_SEND_ERRORS:
            raise  # transitorio: el worker reprograma el job
        # Reintentar no lo arregla y volvería a correr el agente: se registra y se cierra.
        logger.error("WhatsApp no aceptó la respuesta (código %s): %s", error.code, error)
        if case_id is not None:
            audit_log.record(
                conn,
                tenant_id,
                case_id,
                "whatsapp_send_failed",
                {"code": error.code, "in_reply_to": message.wamid},
            )
        forget_sender(conn, job)
        return
    if case_id is not None:
        audit_log.record(
            conn,
            tenant_id,
            case_id,
            "whatsapp_sent",
            {"wamid": wamid, "in_reply_to": message.wamid},
        )
    forget_sender(conn, job)


def production_deps() -> WhatsAppDeps:
    """Agente con los modelos del entorno y la API de Acme simulada en proceso (sandbox)."""
    from app.demo.acme import acme_sandbox_client, build_demo_agent

    agents: dict[int, Agent] = {}
    acme = acme_sandbox_client()

    def agent(conn: psycopg.Connection) -> Agent:
        if id(conn) not in agents:  # el worker usa una sola conexión
            agents[id(conn)] = build_demo_agent(conn, acme)
        return agents[id(conn)]

    return WhatsAppDeps(
        agent=agent, client=WhatsAppClient(), token=os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    )
