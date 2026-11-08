"""Decisión humana sobre una aprobación pendiente (POST /approvals/{id}/decision)."""

import uuid
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from app.db.connection import tenant_scope
from app.policy.guardian import ContactContext, Guardian


class ApprovalError(Exception):
    """La aprobación no existe o ya fue decidida."""


@dataclass(frozen=True)
class ApprovalOutcome:
    status: Literal["executed", "denied", "failed", "rejected"]
    result: dict[str, Any]


def decide(
    conn: psycopg.Connection,
    guardian: Guardian,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    approve: bool,
    decided_by: uuid.UUID | None = None,
) -> ApprovalOutcome:
    """Aprueba (y ejecuta por el guardián) o rechaza. La fila queda bloqueada mientras se decide,
    así dos decisiones simultáneas no ejecutan dos veces."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select a.status, a.case_id, a.params, t.name, c.id, c.phone_hash,"
            " coalesce(c.verified_until > now(), false)"
            " from approvals a"
            " join tools t on t.id = a.tool_id"
            " join cases k on k.id = a.case_id"
            " join contacts c on c.id = k.contact_id"
            " where a.id = %s for update of a",
            (approval_id,),
        ).fetchone()
    if row is None:
        raise ApprovalError("Aprobación inexistente")
    status, case_id, params, tool_name, contact_id, phone_hash, verified = row
    if status != "pending":
        raise ApprovalError(f"La aprobación ya fue decidida ({status})")

    if not approve:
        _close(conn, tenant_id, approval_id, case_id, "rejected", decided_by)
        return ApprovalOutcome("rejected", {})

    decision = guardian.execute_approved(
        tenant_id=tenant_id,
        case_id=case_id,
        contact=ContactContext(id=contact_id, phone_hash=phone_hash, verified=verified),
        tool_name=tool_name,
        tool_input=params,
    )
    _close(conn, tenant_id, approval_id, case_id, "approved", decided_by)
    final = decision.status if decision.status in ("executed", "denied", "failed") else "failed"
    return ApprovalOutcome(final, decision.result)


def _close(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    case_id: uuid.UUID,
    status: str,
    decided_by: uuid.UUID | None,
) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update approvals set status = %s, decided_by = %s, decided_at = now() where id = %s",
            (status, decided_by, approval_id),
        )
        conn.execute(
            "update cases set status = 'open', updated_at = now()"
            " where id = %s and status = 'waiting_approval'",
            (case_id,),
        )
