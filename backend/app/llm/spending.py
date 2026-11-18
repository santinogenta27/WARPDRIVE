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
from decimal import Decimal, InvalidOperation

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


def check_turn(
    conn: psycopg.Connection, tenant_id: uuid.UUID, *, turn_cost: Decimal, turn_calls: int
) -> list[str]:
    limits = budget(conn, tenant_id)
    now = month_spend(conn, tenant_id)
    then = Spend(now.usd - turn_cost, now.calls - turn_calls)
    return alerts_for(tenant_id, before=ratio(then, limits), after=ratio(now, limits))
