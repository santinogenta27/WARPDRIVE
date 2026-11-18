"""Reglas de atención de cada empresa, editables desde el panel (decisión 051).

* **Horario de atención** de las personas del equipo: fuera de horario, cuando el asistente
  deriva, el mensaje fijo dice cuándo responden. El asistente atiende igual las 24 horas.
* **A quién derivar:** el email que recibe el aviso de cada caso derivado (la bandeja, tarea 7).

Se guardan en `tenants.settings.rules` y las aplica el código (el ciclo del agente), nunca el
prompt. Los días van de 1 (lunes) a 7 (domingo); la hora es la de Buenos Aires.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope

ZONE = ZoneInfo("America/Argentina/Buenos_Aires")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DAY_NAMES = {
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
CLOSED_NOTE = {
    "es": "Nuestro equipo atiende de {when}: te responden apenas vuelvan.",
    "en": "Our team is available {when}: they'll reply as soon as they're back.",
}


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Hours:
    days: tuple[int, ...]
    start: str
    end: str


@dataclass(frozen=True)
class Rules:
    hours: Hours | None
    handoff_email: str | None


def _now() -> datetime:
    return datetime.now(ZONE)


def parse_hours(days: str, start: str, end: str) -> Hours:
    try:
        chosen = tuple(sorted({int(d) for d in days.replace(" ", "").split(",") if d}))
    except ValueError as error:
        raise RulesError("Los días del horario no son válidos.") from error
    if not chosen or any(d < 1 or d > 7 for d in chosen):
        raise RulesError("Elegí al menos un día de atención.")
    if not _TIME.match(start) or not _TIME.match(end):
        raise RulesError("El horario va en formato HH:MM, por ejemplo 09:00.")
    if start >= end:
        raise RulesError("La hora de cierre tiene que ser posterior a la de apertura.")
    return Hours(chosen, start, end)


def parse_handoff_email(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not _EMAIL.match(value):
        raise RulesError("El email para derivar no es válido.")
    return value


def is_open(hours: Hours | None, moment: datetime | None = None) -> bool:
    if hours is None:
        return True
    now = (moment or _now()).astimezone(ZONE)
    clock = now.time()
    return now.isoweekday() in hours.days and time.fromisoformat(
        hours.start
    ) <= clock < time.fromisoformat(hours.end)


def describe(hours: Hours, language: str) -> str:
    """ "lunes a viernes de 09:00 a 18:00" o "lunes, miércoles de 09:00 a 13:00"."""
    names = DAY_NAMES.get(language, DAY_NAMES["es"])
    days = list(hours.days)
    consecutive = days == list(range(days[0], days[-1] + 1)) and len(days) > 2
    if language == "en":
        span = (
            f"{names[days[0] - 1]} to {names[days[-1] - 1]}"
            if consecutive
            else ", ".join(names[d - 1] for d in days)
        )
        return f"{span}, {hours.start} to {hours.end}"
    span = (
        f"{names[days[0] - 1]} a {names[days[-1] - 1]}"
        if consecutive
        else ", ".join(names[d - 1] for d in days)
    )
    return f"{span} de {hours.start} a {hours.end}"


def closed_note(conn: psycopg.Connection, tenant_id: uuid.UUID, language: str) -> str | None:
    """Fuera de horario, lo que se le suma al mensaje de derivación."""
    hours = load(conn, tenant_id).hours
    if hours is None or is_open(hours, _now()):
        return None
    template = CLOSED_NOTE.get(language, CLOSED_NOTE["es"])
    return template.format(when=describe(hours, language))


def load(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Rules:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings -> 'rules' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    data = row[0] if row and isinstance(row[0], dict) else {}
    raw = data.get("hours")
    hours = Hours(tuple(raw["days"]), raw["start"], raw["end"]) if raw else None
    return Rules(hours, data.get("handoff_email"))


def save(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    hours: Hours | None,
    handoff_email: str | None,
) -> None:
    value = {
        "hours": {"days": list(hours.days), "start": hours.start, "end": hours.end}
        if hours
        else None,
        "handoff_email": handoff_email,
    }
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"rules": value}), tenant_id),
        )
