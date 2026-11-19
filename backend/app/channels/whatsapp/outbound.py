"""Mensajes salientes que no responden en el momento a un mensaje del cliente.

WhatsApp permite mensajes libres solo hasta 24 h después del último mensaje del cliente
(`contacts.last_customer_message_at`). Fuera de esa ventana se envía la plantilla aprobada
`caso_actualizado` y el texto queda pendiente en `outbound_messages`. Cuando el cliente responde,
el handler lo envía (spec v1.1, "Ventana de 24 horas y plantillas").
"""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import psycopg

from app.audit import log as audit_log
from app.channels.whatsapp.client import OUTSIDE_WINDOW, WhatsAppClient, WhatsAppError
from app.channels.whatsapp.format import to_whatsapp
from app.channels.whatsapp.routing import channel_for_tenant
from app.crypto import decrypt
from app.db.connection import tenant_scope

if TYPE_CHECKING:
    from app.channels.whatsapp.handler import WhatsAppDeps

WINDOW = timedelta(hours=24)
CASE_UPDATED_TEMPLATE = "caso_actualizado"  # "Hola {{1}}, tenemos novedades sobre tu consulta…"


@dataclass(frozen=True)
class Delivery:
    kind: Literal["text", "template"]
    wamid: str


def template_language() -> str:
    return os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE") or "es_AR"


def reply_as_human(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    text: str,
    *,
    deps: "WhatsAppDeps",
    now: datetime | None = None,
    draft: bool = False,
) -> Delivery:
    """Respuesta de una persona del equipo a un caso (tras una derivación). `draft`: un borrador
    del modo supervisado ya aprobado (decisión 072), que ya está en el historial del caso."""
    kind = "draft_delivered" if draft else "human_reply_sent"
    now = now or datetime.now(UTC)
    channel = channel_for_tenant(conn, tenant_id)
    if channel is None:
        raise LookupError("La empresa no tiene un número de WhatsApp asignado")
    phone_number_id, token = channel.phone_number_id, channel.token or deps.token
    text = to_whatsapp(text)
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select c.contact_id, k.last_customer_message_at, k.wa_address_enc"
            " from cases c join contacts k on k.id = c.contact_id where c.id = %s",
            (case_id,),
        ).fetchone()
        if row is None or not row[2]:
            raise LookupError("El caso no tiene un contacto de WhatsApp")
        contact_id, last_in, address_enc = row
        if not draft:
            conn.execute(
                "insert into case_messages (tenant_id, case_id, role, text)"
                " values (%s, %s, 'human', %s)",
                (tenant_id, case_id, text),
            )
    address = json.loads(decrypt(address_enc))
    if last_in is not None and now - last_in < WINDOW:
        try:
            wamid = deps.client.send_text(
                token=token, phone_number_id=phone_number_id, to=address["wa_id"], body=text
            )
        except WhatsAppError as error:
            if error.code != OUTSIDE_WINDOW:
                raise
        else:
            audit_log.record(conn, tenant_id, case_id, kind, {"wamid": wamid, "via": "text"})
            return Delivery("text", wamid)

    wamid = deps.client.send_template(
        token=token,
        phone_number_id=phone_number_id,
        to=address["wa_id"],
        name=CASE_UPDATED_TEMPLATE,
        language=template_language(),
        params=[address.get("name") or "cliente"],
    )
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into outbound_messages (tenant_id, case_id, contact_id, text)"
            " values (%s, %s, %s, %s)",
            (tenant_id, case_id, contact_id, text),
        )
    audit_log.record(
        conn,
        tenant_id,
        case_id,
        kind,
        {"wamid": wamid, "via": "template", "template": CASE_UPDATED_TEMPLATE},
    )
    return Delivery("template", wamid)


def flush_pending(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    *,
    phone_number_id: str,
    to: str,
    client: WhatsAppClient,
    token: str,
) -> int:
    """El cliente volvió a escribir: envía los mensajes pendientes. Devuelve cuántos envió."""
    with tenant_scope(conn, tenant_id):
        pending = conn.execute(
            "select id, case_id, text from outbound_messages"
            " where contact_id = %s and status = 'pending' order by created_at",
            (contact_id,),
        ).fetchall()
    for message_id, case_id, text in pending:
        wamid = client.send_text(token=token, phone_number_id=phone_number_id, to=to, body=text)
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "update outbound_messages set status = 'sent', sent_at = now() where id = %s",
                (message_id,),
            )
        audit_log.record(conn, tenant_id, case_id, "pending_message_sent", {"wamid": wamid})
    return len(pending)
