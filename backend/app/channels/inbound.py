"""Procesa un mensaje entrante de cualquier `Channel`: corre el agente y responde.

`handle_inbound_job` lo hace para los mensajes que encola un webhook (Twilio), dentro de la
transacción del worker: si el envío falla de forma transitoria, se revierte todo y el job se
reintenta. El chat web llama a `handle_inbound` directamente, dentro de su pedido HTTP. La Cloud
API de Meta tiene su propio handler (app/channels/whatsapp/handler.py) por las plantillas y la
ventana de 24 h.
"""

import logging
import uuid
from collections.abc import Callable

import psycopg

from app import i18n
from app.agent.loop import Agent
from app.audit import log as audit_log
from app.cases import supervision
from app.channels import links
from app.channels.base import (
    IGNORED_TYPES,
    UNSUPPORTED_MESSAGE,
    Channel,
    InboundMessage,
    SendError,
    forget_sender,
    touch_contact,
)
from app.channels.whatsapp.routing import channel_for_number
from app.jobs.queue import Job

logger = logging.getLogger(__name__)


def handle_inbound_job(
    conn: psycopg.Connection,
    job: Job,
    *,
    channel: Channel,
    agent: Callable[[psycopg.Connection], Agent],
) -> None:
    handle_inbound(conn, InboundMessage(**job.payload), channel=channel, agent=agent)
    forget_sender(conn, job)  # el teléfono en claro no queda en el job


def handle_inbound(
    conn: psycopg.Connection,
    message: InboundMessage,
    *,
    channel: Channel,
    agent: Callable[[psycopg.Connection], Agent],
) -> bool:
    """Devuelve True si la respuesta quedó como borrador del modo supervisado."""
    route = channel_for_number(conn, message.phone_number_id, kind=channel.kind)
    if route is None:  # número desconocido: se registra en el log y se descarta
        logger.warning(
            "Mensaje de %s para un número sin empresa (%s): se descarta",
            channel.kind,
            message.phone_number_id,
        )
        return False
    tenant_id = route.tenant_id
    contact_id = touch_contact(conn, tenant_id, message)
    if message.type in IGNORED_TYPES:
        return False
    case_id: uuid.UUID | None = None
    if message.type == "text" and message.text.strip():
        result = agent(conn).handle_message(tenant_id, contact_id, message.text)
        reply, case_id = result.message, result.case_id
    elif i18n.tenant_language(conn, tenant_id) == "en":
        reply = i18n.text("en", "unsupported")
    else:
        reply = UNSUPPORTED_MESSAGE
    if case_id is not None and supervision.enabled(conn, tenant_id):
        # Modo supervisado (decisión 072): la respuesta espera a una persona del equipo.
        supervision.hold(conn, tenant_id, case_id, reply, channel=channel.kind)
        return True
    if "whatsapp" in channel.kind:  # Twilio: cada link en su línea, corto (decisión 103)
        reply = links.for_whatsapp(conn, tenant_id, reply)
    try:
        sent_id = channel.send(to=message.sender, body=reply)
    except SendError as error:
        if not error.permanent:
            raise  # transitorio: el worker reprograma el job
        # Reintentar no lo arregla y volvería a correr el agente: se registra y se cierra.
        logger.error("%s no aceptó la respuesta (código %s): %s", channel.kind, error.code, error)
        if case_id is not None:
            audit_log.record(
                conn,
                tenant_id,
                case_id,
                "whatsapp_send_failed",
                {"code": error.code, "in_reply_to": message.wamid, "channel": channel.kind},
            )
        return False
    if case_id is not None:
        audit_log.record(
            conn,
            tenant_id,
            case_id,
            "whatsapp_sent" if "whatsapp" in channel.kind else f"{channel.kind}_sent",
            {"wamid": sent_id, "in_reply_to": message.wamid, "channel": channel.kind},
        )
    return False
