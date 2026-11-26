"""Modo supervisado (Hito 11, decisión 072): la respuesta del agente queda como borrador hasta
que una persona del equipo la envía (tal cual o editada) o la rechaza.

El agente corre completo antes, con el guardián y la auditoría de siempre: el borrador es solo el
texto. Se retiene en el canal (`hold`), se decide en el panel (`send` o `reject`) y se entrega por
el canal del caso (`deliver`).
"""

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

import psycopg
from psycopg.types.json import Jsonb

from app.audit import log as audit_log
from app.db.connection import tenant_scope

SETTING = "supervised"

# (conexión, empresa, caso, canal, texto): envía el texto aprobado por el canal del caso.
Deliver = Callable[[psycopg.Connection, uuid.UUID, uuid.UUID, str, str], None]


class DraftError(Exception):
    """El borrador no existe o ya se decidió (doble clic o reenvío del formulario)."""


@dataclass(frozen=True)
class Draft:
    id: uuid.UUID
    case_id: uuid.UUID
    channel: str
    text: str
    created_at: datetime


def enabled(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    """Apagado salvo que la empresa lo active."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select coalesce((settings ->> %s)::boolean, false) from tenants where id = %s",
            (SETTING, tenant_id),
        ).fetchone()
    return bool(row and row[0])


def set_enabled(conn: psycopg.Connection, tenant_id: uuid.UUID, on: bool) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({SETTING: on}), tenant_id),
        )


def hold(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID, text: str, *, channel: str
) -> uuid.UUID:
    """Guarda la respuesta del agente como borrador. Sale del historial del caso: si nunca llega
    al cliente, el modelo no la ve en el turno siguiente."""
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "delete from case_messages where id = (select id from case_messages"
            " where case_id = %s and role = 'agent' and text = %s"
            " order by created_at desc limit 1)",
            (case_id, text),
        )
        row = conn.execute(
            "insert into reply_drafts (tenant_id, case_id, channel, text)"
            " values (%s, %s, %s, %s) returning id",
            (tenant_id, case_id, channel, text),
        ).fetchone()
    assert row is not None
    draft_id: uuid.UUID = row[0]
    audit_log.record(
        conn,
        tenant_id,
        case_id,
        "draft_held",
        {"draft_id": str(draft_id), "channel": channel, "text": text},
    )
    return draft_id


def pending(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID | None = None
) -> list[Draft]:
    """Borradores por revisar, de los casos sin archivar, del más viejo al más nuevo."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select d.id, d.case_id, d.channel, d.text, d.created_at from reply_drafts d"
            " join cases c on c.id = d.case_id and c.archived_at is null"
            " where d.status = 'pending' and (%s::uuid is null or d.case_id = %s)"
            " order by d.created_at",
            (case_id, case_id),
        ).fetchall()
    return [Draft(*row) for row in rows]


def _decide(
    conn: psycopg.Connection, tenant_id: uuid.UUID, draft_id: uuid.UUID
) -> tuple[uuid.UUID, str, str]:
    """(caso, canal, texto) del borrador pendiente, bloqueado hasta el final de la transacción."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select case_id, channel, text from reply_drafts"
            " where id = %s and status = 'pending' for update",
            (draft_id,),
        ).fetchone()
    if row is None:
        raise DraftError("El borrador no existe o ya se decidió.")
    return row[0], row[1], row[2]


def send(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    draft_id: uuid.UUID,
    text: str,
    *,
    decided_by: uuid.UUID,
    deliver: Deliver,
) -> uuid.UUID:
    """Envía el borrador con `text` (el original o editado). Queda en el historial como respuesta
    del asistente. Si la entrega falla, la excepción revierte todo y sigue pendiente. Devuelve el
    caso."""
    case_id, channel, original = _decide(conn, tenant_id, draft_id)
    text = text.strip() or original
    edited = " ".join(text.split()) != " ".join(original.split())
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into case_messages (tenant_id, case_id, role, text)"
            " values (%s, %s, 'agent', %s) returning id",
            (tenant_id, case_id, text),
        ).fetchone()
        assert row is not None
        conn.execute(
            "update reply_drafts set status = 'sent', final_text = %s, edited = %s,"
            " decided_by = %s, decided_at = now(), message_id = %s where id = %s",
            (text, edited, decided_by, row[0], draft_id),
        )
        conn.execute("update cases set updated_at = now() where id = %s", (case_id,))
    deliver(conn, tenant_id, case_id, channel, text)
    audit_log.record(
        conn,
        tenant_id,
        case_id,
        "draft_sent",
        {"draft_id": str(draft_id), "text": text, "edited": edited, "by": str(decided_by)},
    )
    return case_id


def reject(
    conn: psycopg.Connection, tenant_id: uuid.UUID, draft_id: uuid.UUID, *, decided_by: uuid.UUID
) -> uuid.UUID:
    """No se envía nada. La persona puede responder con el formulario del caso."""
    case_id, _, _ = _decide(conn, tenant_id, draft_id)
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update reply_drafts set status = 'rejected', decided_by = %s, decided_at = now()"
            " where id = %s",
            (decided_by, draft_id),
        )
    audit_log.record(
        conn,
        tenant_id,
        case_id,
        "draft_rejected",
        {"draft_id": str(draft_id), "by": str(decided_by)},
    )
    return case_id


def deliver(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID, channel: str, text: str
) -> None:
    """Entrega de producción. Chat web: nada más, la página lo trae de `/chat/updates`."""
    if channel == "whatsapp":
        from app.channels.whatsapp.client import WhatsAppClient
        from app.channels.whatsapp.handler import WhatsAppDeps
        from app.channels.whatsapp.outbound import reply_as_human

        deps = WhatsAppDeps(
            agent=_no_agent,
            client=WhatsAppClient(),
            token=os.environ.get("WHATSAPP_ACCESS_TOKEN", ""),
        )
        reply_as_human(conn, tenant_id, case_id, text, deps=deps, draft=True)
    elif channel == "twilio_whatsapp":
        from app.channels.twilio.channel import twilio_from_env
        from app.crypto import decrypt

        twilio = twilio_from_env()
        if twilio is None:
            raise LookupError("Falta configurar Twilio")
        with tenant_scope(conn, tenant_id):
            row = conn.execute(
                "select k.wa_address_enc from cases c join contacts k on k.id = c.contact_id"
                " where c.id = %s",
                (case_id,),
            ).fetchone()
        if row is None or not row[0]:
            raise LookupError("El caso no tiene un contacto de WhatsApp")
        from app.channels import links

        body = links.for_whatsapp(conn, tenant_id, text)  # cada link en su línea (decisión 103)
        sid = twilio.send(to=json.loads(decrypt(row[0]))["wa_id"], body=body)
        audit_log.record(
            conn, tenant_id, case_id, "draft_delivered", {"sid": sid, "channel": channel}
        )


def _no_agent(conn: psycopg.Connection) -> NoReturn:
    raise RuntimeError("La entrega de un borrador no corre el agente")
