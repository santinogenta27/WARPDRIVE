"""Ventas atribuidas al agente (Hito 10, punto 5; decisión 063).

Cada link de compra lleva el identificador de su conversación (`ref`): el guardián lo agrega al
armar el carrito (el modelo no lo elige) y anota el link en `checkout_links`. La tienda informa
después si esa compra se completó (`GET /checkouts/{ref}` de la API canónica): eso es una venta
atribuida al agente, con su pedido y su monto. La sincronización es idempotente: una compra
completada se anota una sola vez.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import psycopg

from app.db.connection import tenant_scope

if TYPE_CHECKING:
    from app.policy.guardian import Guardian


def conversation_ref(case_id: uuid.UUID) -> str:
    """El identificador de la conversación en el link: corto, sin datos del cliente."""
    return f"ea-{case_id.hex[:12]}"


def record(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    ref: str,
    checkout: dict[str, Any],
) -> None:
    """Anota el link que armó el agente. El mismo link dos veces es una sola fila."""
    url = str(checkout.get("url") or "")
    if not url:
        return
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into checkout_links (tenant_id, case_id, ref, url, total, currency)"
            " values (%s, %s, %s, %s, %s, %s) on conflict (tenant_id, ref, url) do nothing",
            (
                tenant_id,
                case_id,
                ref,
                url,
                Decimal(str(checkout.get("total") or 0)),
                str(checkout.get("currency") or ""),
            ),
        )


def sync(conn: psycopg.Connection, tenant_id: uuid.UUID, guardian: "Guardian") -> int:
    """Pregunta a la tienda por cada conversación con links sin compra (de los últimos 60
    días) y anota las compras completadas. Devuelve cuántas ventas nuevas encontró."""
    with tenant_scope(conn, tenant_id):
        refs = [
            str(r[0])
            for r in conn.execute(
                "select distinct ref from checkout_links where completed_at is null"
                " and created_at > now() - interval '60 days'"
            ).fetchall()
        ]
    found = 0
    for ref in refs:
        status = guardian.checkout_status(ref)
        if not status or status.get("status") != "completed":
            continue
        with tenant_scope(conn, tenant_id):
            # Una conversación puede tener varios links: la compra se anota en el último.
            row = conn.execute(
                "update checkout_links set order_id = %s, amount = %s, completed_at = now()"
                " where id = (select id from checkout_links where ref = %s"
                " order by created_at desc limit 1) and completed_at is null"
                " and not exists (select 1 from checkout_links where ref = %s"
                " and completed_at is not null) returning id",
                (
                    str(status.get("order_id") or ""),
                    Decimal(str(status.get("total") or 0)),
                    ref,
                    ref,
                ),
            ).fetchone()
        found += row is not None
    return found


@dataclass(frozen=True)
class SalesTotal:
    currency: str
    sales: int
    amount: Decimal
    links: int  # links armados en el período (con o sin compra)


def totals(
    conn: psycopg.Connection, tenant_id: uuid.UUID, since: datetime | None = None
) -> list[SalesTotal]:
    """Ventas atribuidas (cantidad y monto) por moneda, y los links armados."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select currency, count(*) filter (where completed_at is not null),"
            " coalesce(sum(amount) filter (where completed_at is not null), 0), count(*)"
            " from checkout_links where (%s::timestamptz is null or created_at >= %s)"
            " group by currency order by currency",
            (since, since),
        ).fetchall()
    return [SalesTotal(str(r[0]), int(r[1]), Decimal(r[2]), int(r[3])) for r in rows]
