"""Tope de gasto mensual de cada empresa (decisión 049).

* **Con su propia key**, el tope es el que fije la empresa en el panel (sin tope si no fija
  ninguno): el gasto lo paga ella.
* **Con la key de la plataforma**, hay dos límites: el tope en USD (el de la empresa o
  `SHARED_MONTHLY_USD`, el menor) y una cantidad de llamadas al modelo por mes
  (`SHARED_MONTHLY_CALLS`), porque los modelos gratuitos cuestan 0 y el límite real es la cuota.
  Las empresas de demo no tienen límite de llamadas, así la demo no se corta.
* Al cruzar el 80 % hay un aviso; al 100 %, el agente deriva cada mensaje a una persona sin
  llamar al modelo. El mes es el de Buenos Aires, como la facturación (decisión 032).
"""

import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope
from app.llm import tenant_llm
from app.obs import alerts

WARN_AT = 0.8
_MONTH = "date_trunc('month', now() at time zone 'America/Argentina/Buenos_Aires')"


@dataclass(frozen=True)
class Budget:
    cap_usd: Decimal | None
    cap_calls: int | None
    own_key: bool


@dataclass(frozen=True)
class Spend:
    usd: Decimal
    calls: int


def _env_number(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.environ.get(name) or default)
    except InvalidOperation:
        return Decimal(default)


def _setting(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Decimal | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ->> 'monthly_budget_usd' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    return Decimal(row[0]) if row and row[0] is not None else None


def _is_demo(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    from app.companies.connection import mode_for

    return mode_for(conn, tenant_id) == "demo"


def budget(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Budget:
    own = tenant_llm.own_provider(conn, tenant_id) is not None
    chosen = _setting(conn, tenant_id)
    if own:
        return Budget(chosen, None, True)
    shared = _env_number("SHARED_MONTHLY_USD", "5")
    cap = shared if chosen is None else min(chosen, shared)
    calls = None if _is_demo(conn, tenant_id) else int(_env_number("SHARED_MONTHLY_CALLS", "2000"))
    return Budget(cap, calls, False)


def set_budget(conn: psycopg.Connection, tenant_id: uuid.UUID, usd: Decimal | None) -> None:
    """Fija el tope mensual en USD; None lo saca."""
    with tenant_scope(conn, tenant_id):
        if usd is None:
            conn.execute(
                "update tenants set settings = settings - 'monthly_budget_usd' where id = %s",
                (tenant_id,),
            )
        else:
            conn.execute(
                "update tenants set settings = settings || %s where id = %s",
                (Jsonb({"monthly_budget_usd": str(usd)}), tenant_id),
            )


def month_spend(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Spend:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select coalesce(sum(cost_usd), 0), count(*) filter (where kind = 'llm') from usage"
            f" where tenant_id = %s and (created_at at time zone"
            f" 'America/Argentina/Buenos_Aires') >= {_MONTH}",
            (tenant_id,),
        ).fetchone()
    assert row is not None
    return Spend(Decimal(row[0]), int(row[1]))


def ratio(spend: Spend, limits: Budget) -> float:
    """Qué parte del tope se usó: la mayor entre dólares y llamadas (0 si no hay tope)."""
    parts = [0.0]
    if limits.cap_usd is not None:
        parts.append(float(spend.usd / limits.cap_usd) if limits.cap_usd > 0 else 1.0)
    if limits.cap_calls is not None:
        parts.append(spend.calls / limits.cap_calls if limits.cap_calls > 0 else 1.0)
    return max(parts)


def over(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    return ratio(month_spend(conn, tenant_id), budget(conn, tenant_id)) >= 1.0


def alerts_for(tenant_id: uuid.UUID, *, before: float, after: float) -> list[str]:
    """Las alertas de un turno que cruzó el 80 % o el 100 % del tope."""
    found = []
    for mark, kind in ((WARN_AT, "gasto_80"), (1.0, "gasto_100")):
        if before < mark <= after:
            found.append(alerts.alert(kind, tenant=tenant_id, usado=f"{after:.0%}"))
    return found


# -- la vista de gasto (decisión 102) ------------------------------------------------------------
TZ = "America/Argentina/Buenos_Aires"
TOP_CASES = 10


@dataclass(frozen=True)
class DayCost:
    day: date
    usd: Decimal
    calls: int


@dataclass(frozen=True)
class ModelCost:
    model: str
    kind: str
    calls: int
    input_tokens: int
    output_tokens: int
    usd: Decimal
    known: bool  # False: el modelo no tiene precio en la tabla ("price unknown")


@dataclass(frozen=True)
class CaseCost:
    case_id: uuid.UUID
    usd: Decimal
    calls: int
    tokens: int


@dataclass(frozen=True)
class Report:
    year: int
    month: int
    total_usd: Decimal  # sin los modelos sin precio
    calls: int
    unknown_calls: int
    input_tokens: int
    output_tokens: int
    days: list[DayCost]
    models: list[ModelCost]
    top_cases: list[CaseCost]
    conversations: int
    resolved: int
    per_conversation: Decimal | None
    per_resolved: Decimal | None
    projection: Decimal | None  # solo el mes en curso
    budget: Budget
    spend: Spend
    ratio: float


def _bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start, end


def _local(column: str) -> str:
    return f"({column} at time zone '{TZ}')"


def month_report(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    year: int,
    month: int,
    *,
    today: date | None = None,
) -> Report:
    """El gasto de un mes, de la tabla `usage` de la empresa (RLS). Los modelos sin precio en la
    tabla no suman: se cuentan aparte."""
    from app.llm import pricing

    start, end = _bounds(year, month)
    when = _local("created_at")
    with tenant_scope(conn, tenant_id):
        by_model = conn.execute(
            "select model, kind, count(*), coalesce(sum(input_tokens), 0),"
            " coalesce(sum(output_tokens), 0), coalesce(sum(cost_usd), 0) from usage"
            f" where tenant_id = %s and {when} >= %s and {when} < %s"
            " group by model, kind order by sum(cost_usd) desc, count(*) desc",
            (tenant_id, start, end),
        ).fetchall()
        priced = [r[0] for r in by_model if pricing.known(r[0])]
        days = conn.execute(
            f"select {when}::date, coalesce(sum(cost_usd), 0), count(*) from usage"
            f" where tenant_id = %s and {when} >= %s and {when} < %s and model = any(%s)"
            " group by 1 order by 1",
            (tenant_id, start, end, priced),
        ).fetchall()
        cases = conn.execute(
            "select case_id, coalesce(sum(cost_usd), 0), count(*),"
            " coalesce(sum(input_tokens + output_tokens), 0) from usage"
            f" where tenant_id = %s and {when} >= %s and {when} < %s and case_id is not null"
            " and model = any(%s) group by case_id order by 2 desc, 3 desc",
            (tenant_id, start, end, priced),
        ).fetchall()
        closed = _local("closed_at")
        resolved_row = conn.execute(
            "select count(*) from cases where status = 'resolved'"
            f" and {closed} >= %s and {closed} < %s",
            (start, end),
        ).fetchone()
    models = [
        ModelCost(m, k, int(n), int(i), int(o), Decimal(c), pricing.known(m))
        for m, k, n, i, o, c in by_model
    ]
    total = sum((m.usd for m in models if m.known), Decimal(0))
    conversations = len(cases)
    resolved = int(resolved_row[0]) if resolved_row else 0
    projection = None
    day = today or datetime.now(ZoneInfo(TZ)).date()
    if start <= day < end:
        elapsed = (day - start).days + 1
        projection = (total / elapsed * (end - start).days).quantize(Decimal("0.0001"))
    limits = budget(conn, tenant_id)
    spent = month_spend(conn, tenant_id)
    return Report(
        year=year,
        month=month,
        total_usd=total,
        calls=sum(m.calls for m in models),
        unknown_calls=sum(m.calls for m in models if not m.known),
        input_tokens=sum(m.input_tokens for m in models),
        output_tokens=sum(m.output_tokens for m in models),
        days=[DayCost(d, Decimal(c), int(n)) for d, c, n in days],
        models=models,
        top_cases=[
            CaseCost(case, Decimal(c), int(n), int(t)) for case, c, n, t in cases[:TOP_CASES]
        ],
        conversations=conversations,
        resolved=resolved,
        per_conversation=(total / conversations).quantize(Decimal("0.0001")).normalize()
        if conversations
        else None,
        per_resolved=(total / resolved).quantize(Decimal("0.0001")).normalize()
        if resolved
        else None,
        projection=projection,
        budget=limits,
        spend=spent,
        ratio=ratio(spent, limits),
    )


def month_rows(
    conn: psycopg.Connection, tenant_id: uuid.UUID, year: int, month: int
) -> list[tuple[Any, ...]]:
    """Las filas de `usage` del mes, para exportar: (hora local, tipo, modelo, caso, tokens de
    entrada, de salida, de escritura y de lectura de caché, costo)."""
    start, end = _bounds(year, month)
    when = _local("created_at")
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            f"select {when}, kind, model, case_id, input_tokens, output_tokens,"
            " cache_creation_input_tokens, cache_read_input_tokens, cost_usd from usage"
            f" where tenant_id = %s and {when} >= %s and {when} < %s order by created_at",
            (tenant_id, start, end),
        ).fetchall()


def check_turn(
    conn: psycopg.Connection, tenant_id: uuid.UUID, *, turn_cost: Decimal, turn_calls: int
) -> list[str]:
    limits = budget(conn, tenant_id)
    now = month_spend(conn, tenant_id)
    then = Spend(now.usd - turn_cost, now.calls - turn_calls)
    return alerts_for(tenant_id, before=ratio(then, limits), after=ratio(now, limits))
