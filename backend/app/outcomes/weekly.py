"""Informe semanal por email a los admins (Hito 12, tarea 6; decisión 083).

Cada lunes, la semana anterior (lunes a domingo, hora de Buenos Aires): conversaciones,
resoluciones autónomas, derivaciones, CSAT, los temas y motivos de derivación principales, las
preguntas sin respuesta y el enlace al panel.

Idempotente: `weekly_reports` guarda una fila por empresa y semana, insertada en la misma
transacción que el envío; si el envío falla, se revierte y se reintenta en la próxima vuelta del
worker. Activado por defecto (`settings.weekly_report`); las empresas de demo y las que no tienen
admins no lo reciben.
"""

import logging
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.cases.billing import ARGENTINA, current_time
from app.db.connection import tenant_scope
from app.mailer import Email, Mailer
from app.outcomes import analysis, autonomy
from app.panel.labels import type_label

logger = logging.getLogger(__name__)
SETTING = "weekly_report"


def enabled(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings -> %s from tenants where id = %s", (SETTING, tenant_id)
        ).fetchone()
    return not (row and row[0] is False)


def set_enabled(conn: psycopg.Connection, tenant_id: uuid.UUID, on: bool) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = coalesce(settings, '{}'::jsonb) || %s where id = %s",
            (Jsonb({SETTING: on}), tenant_id),
        )


def last_week(now: datetime | None = None) -> tuple[datetime, datetime]:
    """El lunes 00:00 y el lunes siguiente de la semana anterior, en hora de Buenos Aires."""
    moment = (now or current_time()).astimezone(ARGENTINA)
    this_monday = (moment - timedelta(days=moment.weekday())).date()
    start_day = this_monday - timedelta(days=7)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=ARGENTINA)
    return start, start + timedelta(days=7)


def numbers(
    conn: psycopg.Connection, tenant_id: uuid.UUID, start: datetime, end: datetime
) -> dict[str, Any]:
    report = autonomy.between(conn, tenant_id, start, end)
    found = analysis.analyze(conn, tenant_id, since=start, until=end)
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select (select count(*) from cases where created_at >= %(s)s and created_at < %(e)s"
            "  and archived_at is null),"
            " (select count(*) from audit_events where kind = 'escalated'"
            "  and created_at >= %(s)s and created_at < %(e)s),"
            " (select count(*) from csat_responses where answered_at >= %(s)s"
            "  and answered_at < %(e)s and resolved is not null),"
            " (select count(*) from csat_responses where answered_at >= %(s)s"
            "  and answered_at < %(e)s and resolved)",
            {"s": start, "e": end},
        ).fetchone()
    assert row is not None
    return {
        "conversaciones": int(row[0]),
        "resueltos": report.total,
        "autonomas": report.autonomous,
        "derivaciones": int(row[1]),
        "csat_respuestas": int(row[2]),
        "csat_si": int(row[3]),
        "temas": [(t["tipo"], t["casos"]) for t in found["temas"][:3]],
        "motivos": [(d["causa"], d["casos"]) for d in found["derivaciones"][:3]],
        "sin_respuesta": found["sin_respuesta"]["total"],
    }


def compose(company: str, start: datetime, data: dict[str, Any], link: str) -> tuple[str, str]:
    """(asunto, cuerpo) del informe, en texto plano."""
    from app.demo.behind import HANDOFF_LINES

    end = start + timedelta(days=6)
    csat = (
        f"{data['csat_si']} de {data['csat_respuestas']} dijeron que sí"
        if data["csat_respuestas"]
        else "sin respuestas"
    )
    topics = (
        "\n".join(f"  - {type_label(name)}: {n}" for name, n in data["temas"]) or "  - (ninguno)"
    )
    reasons = (
        "\n".join(f"  - {HANDOFF_LINES['es'].get(kind, kind)}: {n}" for kind, n in data["motivos"])
        or "  - (ninguna)"
    )
    subject = f"{company}: tu semana con el asistente ({start:%d/%m} al {end:%d/%m})"
    body = (
        f"Hola, este es el resumen de la semana del {start:%d/%m} al {end:%d/%m} de {company}.\n\n"
        f"Conversaciones: {data['conversaciones']}\n"
        f"Casos resueltos: {data['resueltos']}, de los cuales {data['autonomas']} fueron "
        "resoluciones autónomas (sin derivar, sin reabrir en 72 h y sin CSAT negativo)\n"
        f"Derivaciones a una persona: {data['derivaciones']}\n"
        f"CSAT: {csat}\n"
        f"Preguntas sin respuesta: {data['sin_respuesta']}\n\n"
        f"Temas principales:\n{topics}\n\nMotivos de derivación:\n{reasons}\n\n"
        f"El detalle y el informe mensual exportable: {link}\n\n"
        "Para dejar de recibir este email, desactivalo en Resultados, en el panel."
    )
    return subject, body


def _due(conn: psycopg.Connection) -> list[tuple[uuid.UUID, str]]:
    """Empresas activas con el informe activado, sin las de demo ni las pruebas gratis. Es una
    consulta fuera de un tenant, como la lista de empresas del operador."""
    rows = conn.execute(
        "select id, name from tenants where status = 'active'"
        " and coalesce(settings ->> 'demo', '') = '' and not (settings ? 'trial')"
        " and coalesce((settings ->> %s)::boolean, true) order by created_at",
        (SETTING,),
    ).fetchall()
    return [(r[0], str(r[1])) for r in rows]


def _admins(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[str]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select email from users where role = 'owner' order by created_at"
        ).fetchall()
    return [str(r[0]) for r in rows]


def send_due(conn: psycopg.Connection, mailer: Mailer, now: datetime | None = None) -> int:
    """Manda el informe de la semana anterior a cada empresa que todavía no lo recibió.
    Devuelve cuántas empresas lo recibieron. Cada empresa en su propio savepoint."""
    start, end = last_week(now)
    week: date = start.date()
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    sent = 0
    for tenant_id, company in _due(conn):
        to = _admins(conn, tenant_id)
        if not to:
            continue
        try:
            with conn.transaction():
                with tenant_scope(conn, tenant_id):
                    row = conn.execute(
                        "insert into weekly_reports (tenant_id, week_start, recipients)"
                        " values (%s, %s, %s) on conflict (tenant_id, week_start) do nothing"
                        " returning id",
                        (tenant_id, week, len(to)),
                    ).fetchone()
                if row is None:
                    continue  # ya salió esta semana
                data = numbers(conn, tenant_id, start, end)
                subject, body = compose(company, start, data, f"{base}/panel/resultados")
                with tenant_scope(conn, tenant_id):
                    conn.execute(
                        "update weekly_reports set summary = %s where id = %s",
                        (Jsonb(data), row[0]),
                    )
                for address in to:
                    mailer.send(Email(to=address, subject=subject, body=body))
                sent += 1
        except Exception as error:  # noqa: BLE001 — una empresa que falla no frena a las demás
            logger.warning("No se pudo mandar el informe semanal de %s: %s", tenant_id, error)
    return sent
