"""Archivo de casos para reiniciar la demo (decisión 024).

Los casos no se borran: la auditoría es de solo inserción. Archivados (`archived_at`), salen del
panel y del agente (un cliente que vuelve a escribir abre un caso nuevo) y conservan su cadena
de auditoría. Las aprobaciones pendientes se rechazan, así no se ejecutan después.
"""

import uuid

import psycopg

from app.audit import log as audit_log
from app.db.connection import tenant_scope


def archive_cases(
    conn: psycopg.Connection, tenant_id: uuid.UUID, decided_by: uuid.UUID | None
) -> int:
    """Archiva todos los casos activos del tenant. Devuelve cuántos."""
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update approvals set status = 'rejected', decided_by = %s, decided_at = now()"
            " where status = 'pending'"
            " and case_id in (select id from cases where archived_at is null)",
            (decided_by,),
        )
        rows = conn.execute(
            "update cases set archived_at = now() where archived_at is null returning id"
        ).fetchall()
    for (case_id,) in rows:
        audit_log.record(
            conn, tenant_id, case_id, "case_archived", {"motivo": "Reinicio de la demo"}
        )
    return len(rows)
