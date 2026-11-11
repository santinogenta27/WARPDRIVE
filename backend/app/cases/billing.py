"""Reapertura y conteo para el cobro (spec, "Cierre"; decisión 027).

"Se cuenta como resuelto autónomamente si ningún humano intervino y no se reabre en 7 días. Esa
es la unidad de cobro."

- **Reapertura:** si el cliente vuelve por el mismo tema (el mismo tipo de caso) dentro de los 7
  días de resuelto, el caso se reabre: la conversación sigue en el mismo caso y queda marcado
  `reopened`. Aunque después se resuelva de nuevo, ya no se cobra.
- **Cobrable:** resuelto por el asistente (`resolved_by = 'agent'`), sin reabrir y con los 7 días
  ya cumplidos. Decidir una aprobación no es una intervención humana: es parte del flujo previsto.
"""

import uuid

import psycopg

from app.audit import log as audit_log
from app.db.connection import tenant_scope

REOPEN_DAYS = 7


def reopen_candidate(
    conn: psycopg.Connection, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> tuple[uuid.UUID, str] | None:
    """(caso, tipo de caso) del último caso resuelto del contacto, si se resolvió hace menos de
    7 días y tiene un tipo de caso de la empresa: es el que se reabre si vuelve por lo mismo."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select c.id, t.name from cases c join case_types t on t.id = c.case_type_id"
            " where c.contact_id = %s and c.status = 'resolved' and c.archived_at is null"
            " and c.closed_at > now() - make_interval(days => %s)"
            " order by c.closed_at desc limit 1",
            (contact_id, REOPEN_DAYS),
        ).fetchone()
    return (row[0], str(row[1])) if row else None


def reopen(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID) -> None:
    """Reabre el caso: vuelve a estar abierto y deja de contar como resuelto por el asistente."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "update cases set status = 'open', reopened = true, resolved_by = null,"
            " closed_at = null, updated_at = now() where id = %s and status = 'resolved'"
            " returning 1",
            (case_id,),
        ).fetchone()
    if row is not None:
        audit_log.record(
            conn,
            tenant_id,
            case_id,
            "case_reopened",
            {"motivo": f"El cliente volvió por el mismo tema dentro de los {REOPEN_DAYS} días."},
        )


def billing_summary(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, int]:
    """Casos de la empresa según la regla de cobro:

    - `cobrables`: resueltos por el asistente, sin reabrir, con los 7 días cumplidos.
    - `en_ventana`: resueltos por el asistente hace menos de 7 días (todavía pueden reabrirse).
    - `con_persona`: resueltos con intervención de una persona (no se cobran).
    - `reabiertos`: el cliente volvió por el mismo tema (no se cobran)."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select"
            " count(*) filter (where status = 'resolved' and resolved_by = 'agent'"
            "  and not reopened and closed_at <= now() - make_interval(days => %s)),"
            " count(*) filter (where status = 'resolved' and resolved_by = 'agent'"
            "  and not reopened and closed_at > now() - make_interval(days => %s)),"
            " count(*) filter (where status = 'resolved' and resolved_by = 'human'"
            "  and not reopened),"
            " count(*) filter (where reopened)"
            " from cases where archived_at is null",
            (REOPEN_DAYS, REOPEN_DAYS),
        ).fetchone()
    assert row is not None
    return {
        "cobrables": row[0],
        "en_ventana": row[1],
        "con_persona": row[2],
        "reabiertos": row[3],
    }
