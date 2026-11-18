"""Ventas cruzadas con reglas (Hito 10, punto 3; decisión 061).

La empresa elige en el panel qué producto ofrecer, en qué tipos de caso y con qué descuento
máximo. El guardián (`app/policy/guardian.py`) aplica todo en código: una oferta solo con una
regla activa para el tipo de caso, hasta su descuento máximo, una sola por conversación y nunca
con el cliente enojado o en medio de un reclamo.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
import yaml

from app.agent import conduct
from app.db.connection import tenant_scope

OFFER_PRODUCT = "ofrecer_producto"  # la herramienta interna de la oferta
MAX_DISCOUNT = Decimal(50)  # nadie regala más de la mitad desde el chat


class UpsellError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: uuid.UUID
    product: str
    case_types: list[str]
    max_discount: float
    active: bool


def load(
    conn: psycopg.Connection, tenant_id: uuid.UUID, *, active_only: bool = False
) -> list[Rule]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, product, case_types, max_discount, active from upsell_rules"
            " where (not %s or active) order by created_at",
            (active_only,),
        ).fetchall()
    return [Rule(r[0], r[1], list(r[2] or []), float(r[3]), bool(r[4])) for r in rows]


def parse_discount(raw: str) -> Decimal:
    text = (raw or "0").strip().replace(",", ".").removesuffix("%").strip() or "0"
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise UpsellError("El descuento tiene que ser un número.") from error
    if value < 0 or value > MAX_DISCOUNT:
        raise UpsellError(f"El descuento máximo va de 0 a {MAX_DISCOUNT} %.")
    return value


def add(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    product: str,
    case_types: list[str],
    max_discount: Decimal,
) -> uuid.UUID:
    """Una regla nueva. Solo en tipos de caso que no son reclamos."""
    name = " ".join(product.split())
    if not name:
        raise UpsellError("Falta el producto.")
    if not case_types:
        raise UpsellError("Elegí al menos un tipo de caso.")
    allowed = set(eligible_types(conn, tenant_id))
    wrong = [t for t in case_types if t not in allowed]
    if wrong:
        raise UpsellError("Las ventas no se ofrecen en un reclamo: elegí otro tipo de caso.")
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into upsell_rules (tenant_id, product, case_types, max_discount)"
            " values (%s, %s, %s, %s) returning id",
            (tenant_id, name, case_types, max_discount),
        ).fetchone()
    assert row is not None
    rule_id: uuid.UUID = row[0]
    return rule_id


def remove(conn: psycopg.Connection, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
    with tenant_scope(conn, tenant_id):
        found = conn.execute(
            "delete from upsell_rules where id = %s returning id", (rule_id,)
        ).fetchone()
    return found is not None


def _tools_of(instructions: str) -> list[str]:
    try:
        parsed = yaml.safe_load(instructions or "") or {}
    except yaml.YAMLError:
        return []
    return [str(t) for t in parsed.get("herramientas") or []] if isinstance(parsed, dict) else []


def complaint_types(conn: psycopg.Connection, tenant_id: uuid.UUID) -> set[str]:
    """Tipos de caso que son un reclamo: usan una herramienta que modifica un pedido del
    cliente (reembolso, cancelación, cambio, cupón por un problema)."""
    with tenant_scope(conn, tenant_id):
        kinds = conn.execute("select name, instructions from case_types").fetchall()
        tools: dict[str, Any] = {
            str(r[0]): r[1] for r in conn.execute("select name, manifest from tools").fetchall()
        }
    found = set()
    for name, instructions in kinds:
        for tool in _tools_of(instructions):
            manifest = tools.get(tool) or {}
            method = (manifest.get("upstream") or {}).get("method")
            if manifest.get("owner_check") and method != "GET":
                found.add(str(name))
    return found


def eligible_types(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[str]:
    """Tipos de caso activos donde se puede ofrecer algo (los que no son reclamos)."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute("select name from case_types where active order by name").fetchall()
    complaints = complaint_types(conn, tenant_id)
    return [str(r[0]) for r in rows if r[0] not in complaints]


def bad_moment(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID, message: str
) -> str | None:
    """Por qué no es momento de ofrecer nada, o None. El cliente está enojado (en este mensaje
    o en uno anterior del caso) o en medio de un reclamo (el tipo de caso es un reclamo, el caso
    espera una aprobación o se derivó)."""
    with tenant_scope(conn, tenant_id):
        said = conn.execute(
            "select text from case_messages where case_id = %s and role = 'customer'", (case_id,)
        ).fetchall()
        row = conn.execute(
            "select c.status, t.name from cases c left join case_types t on t.id = c.case_type_id"
            " where c.id = %s",
            (case_id,),
        ).fetchone()
    if any(conduct.upset_any_language(text) for text in [message, *(r[0] for r in said)]):
        return "El cliente está molesto."
    status, kind = row or (None, None)
    if status in ("waiting_approval", "escalated"):
        return "El caso está en medio de un reclamo."
    if kind is not None and kind in complaint_types(conn, tenant_id):
        return "El caso está en medio de un reclamo."
    return None


def offered(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID) -> bool:
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select 1 from upsell_offers where case_id = %s", (case_id,)).fetchone()
    return row is not None


def offerable(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    case_type: str | None,
    message: str,
) -> list[Rule]:
    """Las reglas que se pueden ofrecer ahora: activas para el tipo de caso, sin una oferta ya
    hecha en la conversación y en buen momento. Vacío: no se ofrece la herramienta."""
    if case_type is None:
        return []
    rules = [r for r in load(conn, tenant_id, active_only=True) if case_type in r.case_types]
    if not rules or offered(conn, tenant_id, case_id):
        return []
    if bad_moment(conn, tenant_id, case_id, message) is not None:
        return []
    return rules
