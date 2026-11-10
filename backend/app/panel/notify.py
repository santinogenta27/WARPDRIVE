"""Mensajes de una persona del equipo al cliente (respuesta desde el panel o aviso de una
aprobación). Quedan en el caso como rol `human` y en la auditoría.

En el chat web, la página los trae cada pocos segundos (GET /chat/updates). Por WhatsApp se
envían con `outbound.reply_as_human` cuando el canal está activo (hoy en pausa).
"""

import uuid
from typing import Any

import psycopg

from app.audit import log as audit_log
from app.db.connection import tenant_scope


def notify_customer(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    text: str,
    *,
    kind: str = "human_reply",
    detail: dict[str, Any] | None = None,
) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into case_messages (tenant_id, case_id, role, text)"
            " values (%s, %s, 'human', %s)",
            (tenant_id, case_id, text),
        )
        conn.execute("update cases set updated_at = now() where id = %s", (case_id,))
    audit_log.record(conn, tenant_id, case_id, kind, {"text": text, **(detail or {})})
