"""Aviso por email al equipo de la empresa (decisión 054): un caso derivado o una aprobación
pendiente que espera a una persona.

* **A quién:** el email "a quién derivar" de las reglas (decisión 051); si no hay, los usuarios
  de la empresa.
* **Una sola vez** por caso derivado y por aprobación: queda `team_notified` en la auditoría
  del caso, sin las direcciones.
* **Sin datos del cliente:** el email trae el tipo de caso, el motivo y el enlace al panel, no lo
  que escribió el cliente. Se responde desde el panel.
* Un problema con el email nunca corta el turno; sale en otro hilo, como las alertas.
"""

import logging
import os
import threading
import uuid
from typing import Literal

import psycopg

from app.audit import log as audit_log
from app.companies import rules
from app.db.connection import tenant_scope
from app.mailer import ConsoleMailer, Email, Mailer, mailer_from_env

logger = logging.getLogger("app.team_notify")
NOTIFIED = "team_notified"
BACKGROUND = True
Kind = Literal["escalated", "approval"]
SUBJECTS = {
    "escalated": "[{company}] Un caso espera a una persona",
    "approval": "[{company}] Una acción espera tu aprobación",
}
BODIES = {
    "escalated": "El asistente derivó un caso: {reason}\n\nRespondelo desde el panel: {link}",
    "approval": "El asistente pidió aprobar una acción: {reason}\n\nAprobala o rechazala desde "
    "el panel: {link}",
}


def _mailer() -> Mailer:
    return mailer_from_env()


def recipients(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[str]:
    from app.companies.connection import mode_for

    chosen = rules.load(conn, tenant_id).handoff_email
    if not chosen and mode_for(conn, tenant_id) == "demo":
        return []  # Acme y Northwind: la demo no le escribe a nadie
    if chosen:
        return [chosen]
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select email from users where role in ('owner', 'agent') order by created_at"
        ).fetchall()
    return [str(r[0]) for r in rows]


def _already(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID, key: str) -> bool:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select 1 from audit_events where case_id = %s and kind = %s"
            " and payload ->> 'key' = %s limit 1",
            (case_id, NOTIFIED, key),
        ).fetchone()
    return row is not None


def notify(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    kind: Kind,
    *,
    reason: str,
    approval_id: uuid.UUID | None = None,
) -> int:
    """Avisa al equipo. Devuelve a cuántas direcciones se envió (0 si ya se había avisado)."""
    key = f"{kind}:{approval_id or case_id}"
    if _already(conn, tenant_id, case_id, key):
        return 0
    to = recipients(conn, tenant_id)
    if not to:
        return 0  # nadie a quién avisar (las empresas de demo): no queda nada en la auditoría
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select name from tenants where id = %s", (tenant_id,)).fetchone()
    company = str(row[0]) if row else ""
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    path = f"/panel/casos/{case_id}" if kind == "escalated" else "/panel/aprobaciones"
    subject = SUBJECTS[kind].format(company=company)
    body = BODIES[kind].format(reason=reason, link=f"{base}{path}")
    audit_log.record(conn, tenant_id, case_id, NOTIFIED, {"key": key, "destinatarios": len(to)})
    emails = [Email(to=address, subject=subject, body=body) for address in to]

    def deliver() -> None:
        try:
            mailer = _mailer()
            for email in emails:
                if isinstance(mailer, ConsoleMailer):
                    logger.info("Aviso al equipo (sin proveedor de email): %s", subject)
                    return
                mailer.send(email)
        except Exception as error:  # noqa: BLE001 — avisar no puede romper el turno
            logger.warning("No se pudo avisar al equipo por email: %s", error)

    if BACKGROUND:
        threading.Thread(target=deliver, name="aviso-equipo", daemon=True).start()
    else:
        deliver()
    return len(emails)
