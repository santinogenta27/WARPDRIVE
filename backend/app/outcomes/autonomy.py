"""Resolución autónoma auditable (Hito 12, tarea 6; decisión 083).

Un caso resuelto es una resolución autónoma si:

1. nunca se derivó;
2. lo resolvió el asistente;
3. no se reabrió dentro de las 72 h del cierre;
4. el cliente no respondió que no se resolvió (CSAT negativo).

Cerrado hace menos de 72 h queda "en ventana". Cada caso tiene un estado y el informe se exporta
como CSV, un renglón por caso, sin datos del cliente: con el id se audita en el panel.
"""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.cases.billing import current_time, month_bounds
from app.db.connection import tenant_scope

WINDOW_HOURS = 72
STATES = ("autonoma", "en_ventana", "derivada", "con_persona", "reabierta_72h", "csat_negativo")
LABELS = {
    "es": {
        "autonoma": "Resolución autónoma",
        "en_ventana": "En ventana (menos de 72 h)",
        "derivada": "Derivada a una persona",
        "con_persona": "Resuelta por una persona",
        "reabierta_72h": "Reabierta dentro de las 72 h",
        "csat_negativo": "El cliente dijo que no se resolvió",
    },
    "en": {
        "autonoma": "Autonomous resolution",
        "en_ventana": "In the window (under 72 h)",
        "derivada": "Handed off to a person",
        "con_persona": "Resolved by a person",
        "reabierta_72h": "Reopened within 72 h",
        "csat_negativo": "The customer said it wasn't resolved",
    },
}


@dataclass(frozen=True)
class CaseOutcome:
    case_id: uuid.UUID
    case_type: str | None
    created_at: datetime
    closed_at: datetime
    escalated: bool
    by_agent: bool
    reopened_72h: bool
    csat_negative: bool
    state: str


@dataclass(frozen=True)
class Report:
    start: datetime
    end: datetime
    cases: list[CaseOutcome]

    @property
    def counts(self) -> dict[str, int]:
        found = dict.fromkeys(STATES, 0)
        for case in self.cases:
            found[case.state] += 1
        return found

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def autonomous(self) -> int:
        return self.counts["autonoma"]

    @property
    def rate(self) -> float:
        """Resoluciones autónomas sobre los casos ya definidos (fuera de la ventana de 72 h)."""
        settled = self.total - self.counts["en_ventana"]
        return round(self.autonomous / settled, 4) if settled else 0.0


def _state(escalated: bool, by_agent: bool, reopened: bool, negative: bool, recent: bool) -> str:
    if escalated:
        return "derivada"
    if not by_agent:
        return "con_persona"
    if reopened:
        return "reabierta_72h"
    if negative:
        return "csat_negativo"
    return "en_ventana" if recent else "autonoma"


def between(
    conn: psycopg.Connection, tenant_id: uuid.UUID, start: datetime, end: datetime
) -> Report:
    """Los casos resueltos en [start, end) con su estado. Una reapertura sin las horas desde el
    cierre (anterior a la decisión 083) cuenta como dentro de las 72 h."""
    now = current_time()
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select c.id, t.name, c.created_at, c.closed_at, c.escalation is not null,"
            " coalesce(c.resolved_by = 'agent', false),"
            " exists (select 1 from audit_events e where e.case_id = c.id"
            "  and e.kind = 'case_reopened'"
            "  and coalesce((e.payload ->> 'horas_desde_cierre')::numeric, 0) <= %(hours)s),"
            " exists (select 1 from csat_responses r where r.case_id = c.id"
            "  and r.resolved is false),"
            " c.closed_at > %(now)s - make_interval(hours => %(hours)s)"
            " from cases c left join case_types t on t.id = c.case_type_id"
            " where c.status = 'resolved' and c.archived_at is null"
            " and c.closed_at >= %(start)s and c.closed_at < %(end)s"
            " order by c.closed_at, c.id",
            {"start": start, "end": end, "now": now, "hours": WINDOW_HOURS},
        ).fetchall()
    cases = [
        CaseOutcome(
            case_id=r[0],
            case_type=r[1],
            created_at=r[2],
            closed_at=r[3],
            escalated=bool(r[4]),
            by_agent=bool(r[5]),
            reopened_72h=bool(r[6]),
            csat_negative=bool(r[7]),
            state=_state(bool(r[4]), bool(r[5]), bool(r[6]), bool(r[7]), bool(r[8])),
        )
        for r in rows
    ]
    return Report(start, end, cases)


def month(conn: psycopg.Connection, tenant_id: uuid.UUID, year: int, month_: int) -> Report:
    start, end = month_bounds(year, month_)
    return between(conn, tenant_id, start, end)


def to_csv(report: Report) -> str:
    """Un renglón por caso, sin datos del cliente."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "caso",
            "tipo_de_caso",
            "abierto",
            "cerrado",
            "derivado",
            "resuelto_por_el_asistente",
            "reabierto_en_72h",
            "csat_negativo",
            "estado",
        ]
    )
    yes = {True: "si", False: "no"}
    for case in report.cases:
        writer.writerow(
            [
                str(case.case_id),
                case.case_type or "",
                case.created_at.isoformat(timespec="seconds"),
                case.closed_at.isoformat(timespec="seconds"),
                yes[case.escalated],
                yes[case.by_agent],
                yes[case.reopened_72h],
                yes[case.csat_negative],
                case.state,
            ]
        )
    return buffer.getvalue()
