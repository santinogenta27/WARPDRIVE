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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import psycopg

from app.audit import log as audit_log
from app.db.connection import tenant_scope

REOPEN_DAYS = 7
# Hora de Buenos Aires, sin horario de verano: el mes de facturación es el de la Argentina.
ARGENTINA = timezone(timedelta(hours=-3))
STATES = ("cobrables", "en_ventana", "con_persona", "reabiertos")
# Estado de cobro de un caso resuelto (decisión 032). Parámetro: el momento "ahora".
_STATE = (
    "case when c.reopened then 'reabierto'"
    " when c.resolved_by is distinct from 'agent' then 'con_persona'"
    f" when c.closed_at <= %(now)s - interval '{REOPEN_DAYS} days' then 'cobrable'"
    " else 'en_ventana' end"
)
# Un caso entra en el mes en que se resolvió. Los archivados (demo reiniciada) no cuentan.
_IN_PERIOD = (
    "c.status = 'resolved' and c.archived_at is null"
    " and c.closed_at >= %(start)s and c.closed_at < %(end)s"
)


def current_time() -> datetime:
    return datetime.now(ARGENTINA)


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Primer instante del mes y del mes siguiente, en hora de Buenos Aires."""
    start = datetime(year, month, 1, tzinfo=ARGENTINA)
    following = date(year + (month == 12), month % 12 + 1, 1)
    return start, datetime(following.year, following.month, 1, tzinfo=ARGENTINA)


@dataclass(frozen=True)
class BilledCase:
    case_id: uuid.UUID
    case_type: str | None
    created_at: datetime
    closed_at: datetime
    resolved_by: str | None
    state: str  # cobrable, en_ventana, con_persona o reabierto


@dataclass(frozen=True)
class CompanyPeriod:
    tenant_id: uuid.UUID
    name: str
    counts: dict[str, int]


def _period(year: int, month: int) -> dict[str, datetime]:
    start, end = month_bounds(year, month)
    return {"start": start, "end": end, "now": current_time()}


def _counts(alias: str = "s") -> str:
    names = {"cobrables": "cobrable", "en_ventana": "en_ventana"}
    names |= {"con_persona": "con_persona", "reabiertos": "reabierto"}
    return ", ".join(f"count(*) filter (where {alias}.state = '{names[key]}')" for key in STATES)


def period_summary(
    conn: psycopg.Connection, tenant_id: uuid.UUID, year: int, month: int
) -> dict[str, int]:
    """Casos de la empresa resueltos en el mes, según la regla de cobro."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            f"select {_counts()} from (select {_STATE} as state from cases c where {_IN_PERIOD}) s",
            _period(year, month),
        ).fetchone()
    assert row is not None
    return dict(zip(STATES, row, strict=True))


def period_cases(
    conn: psycopg.Connection, tenant_id: uuid.UUID, year: int, month: int
) -> list[BilledCase]:
    """El detalle del mes: un renglón por caso resuelto, sin datos del cliente."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            f"select c.id, t.name, c.created_at, c.closed_at, c.resolved_by, {_STATE}"
            " from cases c left join case_types t on t.id = c.case_type_id"
            f" where {_IN_PERIOD} order by c.closed_at, c.id",
            _period(year, month),
        ).fetchall()
    return [BilledCase(*row) for row in rows]


def period_by_company(conn: psycopg.Connection, year: int, month: int) -> list[CompanyPeriod]:
    """El mes de todas las empresas activas. Es una consulta fuera de un tenant: solo la usa el
    panel del operador de la plataforma, como la lista de empresas."""
    rows = conn.execute(
        f"select t.id, t.name, {_counts()} from tenants t left join ("
        f" select c.tenant_id, {_STATE} as state from cases c where {_IN_PERIOD}"
        ") s on s.tenant_id = t.id where t.status = 'active' group by t.id, t.name"
        " order by lower(t.name)",
        _period(year, month),
    ).fetchall()
    return [
        CompanyPeriod(row[0], str(row[1]), dict(zip(STATES, row[2:], strict=True))) for row in rows
    ]


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
