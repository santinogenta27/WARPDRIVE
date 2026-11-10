"""Regla de cierre de la spec ("Cierre"), la unidad de cobro (decisión 024).

Un caso queda resuelto cuando:
- el cliente confirma ("gracias", "listo, eso era todo") después de una respuesta, o
- pasan 24 h sin respuesta del cliente desde el último mensaje del asistente o del equipo.

Un caso derivado no se cierra solo hasta que una persona le respondió; uno que espera una
aprobación, nunca. `resolved_by` es `human` si una persona intervino (derivación o respuesta desde
el panel) y `agent` si no. Decidir una aprobación es parte del flujo previsto y no cuenta como
intervención.
"""

import re
import uuid

import psycopg

from app.audit import log as audit_log
from app.db.connection import tenant_scope

INACTIVE_HOURS = 24
CLOSING_MESSAGE = "¡De nada! Si necesitás algo más, escribime cuando quieras."
HUMAN_KINDS = ["human_reply", "escalated"]

_THANKS = (
    r"(?:(?:muchas|mil)\s+)?gracias|genial|perfecto|listo|joya|excelente|b[aá]rbaro|buen[ií]simo"
)
_DONE = r"eso\s+era\s+todo|nada\s+m[aá]s|ya\s+est[aá]|(?:est[aá]\s+)?resuelto|solucionado"
_PART = rf"(?:{_THANKS}|{_DONE})"
CONFIRMATION = re.compile(rf"^\s*¡?{_PART}(?:[\s,!.¡]+{_PART})*[\s!.]*$", re.IGNORECASE)


def is_confirmation(text: str) -> bool:
    """El mensaje entero es un agradecimiento o un "eso era todo" (no trae otra consulta)."""
    return CONFIRMATION.match(text.strip()) is not None


def resolve(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID, reason: str
) -> str | None:
    """Marca el caso resuelto y lo registra en la auditoría. Devuelve quién lo resolvió."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "update cases set status = 'resolved', closed_at = now(), updated_at = now(),"
            " resolved_by = case when exists (select 1 from audit_events e"
            "  where e.case_id = cases.id and e.kind = any(%s)) then 'human' else 'agent' end"
            " where id = %s and status <> 'resolved' returning resolved_by",
            (HUMAN_KINDS, case_id),
        ).fetchone()
    if row is None:
        return None
    audit_log.record(
        conn, tenant_id, case_id, "case_resolved", {"motivo": reason, "resuelto_por": row[0]}
    )
    return str(row[0])


def closable_by_confirmation(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> bool:
    """Abierto o derivado, y ya hubo una respuesta del asistente o del equipo."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select c.status in ('open', 'escalated') and exists (select 1 from case_messages m"
            " where m.case_id = c.id and m.role in ('agent', 'human'))"
            " from cases c where c.id = %s",
            (case_id,),
        ).fetchone()
    return bool(row and row[0])


def close_inactive(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    contact_id: uuid.UUID | None = None,
    hours: int = INACTIVE_HOURS,
) -> int:
    """Resuelve los casos sin respuesta del cliente hace `hours` horas. Devuelve cuántos."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select c.id from cases c join lateral (select m.role, m.created_at"
            "  from case_messages m where m.case_id = c.id"
            "  order by m.created_at desc limit 1) last on true"
            " where c.status in ('open', 'escalated')"
            " and (%s::uuid is null or c.contact_id = %s)"
            " and last.role in ('agent', 'human')"
            " and last.created_at < now() - make_interval(hours => %s)"
            " and (c.status = 'open' or last.role = 'human')",
            (contact_id, contact_id, hours),
        ).fetchall()
    for (case_id,) in rows:
        resolve(conn, tenant_id, case_id, f"{hours} h sin respuesta del cliente")
    return len(rows)
