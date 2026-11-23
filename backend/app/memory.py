"""Memoria del cliente (Hito 12, decisión 078): de conversaciones a relaciones.

Un perfil por empresa y por cliente verificado (`customer_key`: el hash del teléfono que el
cliente demostró, el mismo que usa el control de dueño del guardián). Se arma en código, sin
modelo, con lo que el turno ya tiene:

- nombre y compras: solo de pedidos que pasaron el control de dueño, con una lista blanca de
  campos (nunca medio de pago, montos, dirección ni teléfono);
- preferencias: frases del cliente ("soy talle 38", "my size is M"), sin datos de pago;
- conversaciones anteriores: una línea por caso (fecha, tema, pedido, productos y resultado);
- casos abiertos: se calculan al leer, con el estado actual de cada caso.

El agente la recibe solo con el contacto verificado. La retención se configura por empresa
(`settings.memory_retention_months`, 12 por defecto): lo vencido se borra al leer.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope

DEFAULT_RETENTION_MONTHS = 12
MIN_RETENTION_MONTHS, MAX_RETENTION_MONTHS = 1, 36
MAX_ENTRIES = 10  # compras y casos que se guardan
MAX_PREFERENCES = 10
MAX_PREFERENCE_CHARS = 120
MAX_NAME_CHARS = 120

# Cómo terminó el turno, por la acción final del agente.
OUTCOMES = {
    "responder": "respondido",
    "ejecutar": "accion",
    "pedir_aprobacion": "en_revision",
    "derivar": "derivado",
}
_OUTCOME_TEXT = {
    "es": {
        "respondido": "respondido",
        "accion": "resuelto con una acción",
        "en_revision": "en revisión del equipo",
        "derivado": "derivado al equipo",
    },
    "en": {
        "respondido": "answered",
        "accion": "solved with an action",
        "en_revision": "waiting for the team's review",
        "derivado": "handed off to the team",
    },
}

_PREFERENCE = re.compile(
    r"(?i)\b(?:prefiero|preferir[ií]a|me gustan?\s+m[aá]s|soy\s+talle|uso\s+(?:el\s+)?talle"
    r"|mi\s+talle\s+es|calzo"
    r"|i\s+prefer|i'?d\s+prefer|i'?d\s+rather|my\s+size\s+is|i\s+wear(?:\s+a)?\s+size"
    r"|i\s+usually\s+wear|i'?m\s+a\s+size)\b[^.!?\n]{0,100}"
)
# Datos de pago o números largos (tarjetas, cuentas, documentos): la frase no se guarda.
_PAYMENT = re.compile(
    r"(?i)tarjeta|cr[eé]dito|d[eé]bito|\bcbu\b|\bcvu\b|\bcvv\b|\biban\b|\bvisa\b|master\s?card"
    r"|\bamex\b|american\s+express|\bcard\b|paypal|mercado\s?pago|cuenta\s+bancaria|\bbank\b"
    r"|\bpag(?:o|ar|u[eé])\w*|\bpay\w*|\d[\d\s.-]{6,}\d"
)


@dataclass
class Memory:
    id: uuid.UUID
    name: str
    preferences: list[str]
    purchases: list[dict[str, Any]]
    history: list[dict[str, Any]]
    last_seen_at: datetime
    open_cases: list[dict[str, Any]] = field(default_factory=list)


# -- retención -----------------------------------------------------------------------------------
def retention_months(conn: psycopg.Connection, tenant_id: uuid.UUID) -> int:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ->> 'memory_retention_months' from tenants where id = %s",
            (tenant_id,),
        ).fetchone()
    try:
        months = int(row[0]) if row and row[0] is not None else DEFAULT_RETENTION_MONTHS
    except ValueError:
        return DEFAULT_RETENTION_MONTHS
    return min(max(months, MIN_RETENTION_MONTHS), MAX_RETENTION_MONTHS)


def set_retention(conn: psycopg.Connection, tenant_id: uuid.UUID, months: int) -> None:
    if not MIN_RETENTION_MONTHS <= months <= MAX_RETENTION_MONTHS:
        raise ValueError(
            f"La retención va de {MIN_RETENTION_MONTHS} a {MAX_RETENTION_MONTHS} meses."
        )
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = coalesce(settings, '{}'::jsonb) || %s where id = %s",
            (Jsonb({"memory_retention_months": months}), tenant_id),
        )


def purge_expired(conn: psycopg.Connection, tenant_id: uuid.UUID) -> int:
    """Borra las memorias sin actividad en el plazo de retención de la empresa."""
    months = retention_months(conn, tenant_id)
    with tenant_scope(conn, tenant_id):
        cursor = conn.execute(
            "delete from customer_memories"
            " where last_seen_at < now() - make_interval(months => %s)",
            (months,),
        )
    return cursor.rowcount


# -- lectura -------------------------------------------------------------------------------------
_COLUMNS = "id, name, preferences, purchases, history, last_seen_at"


def _memory(row: tuple[Any, ...]) -> Memory:
    return Memory(
        id=row[0],
        name=str(row[1] or ""),
        preferences=list(row[2] or []),
        purchases=list(row[3] or []),
        history=list(row[4] or []),
        last_seen_at=row[5],
    )


def load(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    customer_key: str,
    *,
    exclude_case: uuid.UUID | None = None,
) -> Memory | None:
    """La memoria del cliente verificado, con sus casos abiertos (sin el del turno). Quien llama
    es responsable de pasar la clave del contacto verificado: nunca otra."""
    purge_expired(conn, tenant_id)
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            f"select {_COLUMNS} from customer_memories where customer_key = %s",
            (customer_key,),
        ).fetchone()
        if row is None:
            return None
        found = _memory(row)
        if exclude_case is not None:  # la conversación en curso no es "anterior"
            found.history = [h for h in found.history if h.get("caso") != str(exclude_case)]
        ids = [str(h.get("caso")) for h in found.history if h.get("caso")]
        statuses: dict[str, str] = dict(
            conn.execute(
                "select id::text, status from cases where id::text = any(%s)"
                " and status <> 'resolved' and archived_at is null",
                (ids,),
            ).fetchall()
            if ids
            else []
        )
    found.open_cases = [
        {**h, "estado": statuses[str(h["caso"])]}
        for h in found.history
        if str(h.get("caso")) in statuses
    ]
    return found


def list_all(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[Memory]:
    purge_expired(conn, tenant_id)
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            f"select {_COLUMNS} from customer_memories order by last_seen_at desc limit 500"
        ).fetchall()
    return [_memory(r) for r in rows]


def get(conn: psycopg.Connection, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> Memory | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            f"select {_COLUMNS} from customer_memories where id = %s", (memory_id,)
        ).fetchone()
    return _memory(row) if row else None


# -- escritura -----------------------------------------------------------------------------------
def purchase_from(order: dict[str, Any]) -> dict[str, Any] | None:
    """Una compra con la lista blanca de campos: número, productos, estado y fecha."""
    order_id = order.get("id")
    items = order.get("items")
    if not isinstance(order_id, str | int) or not isinstance(items, list):
        return None
    products = [str(i.get("name")) for i in items if isinstance(i, dict) and i.get("name")]
    return {
        "pedido": str(order_id),
        "productos": products[:5],
        "estado": str(order.get("status") or ""),
        "fecha": str(order.get("created_at") or "")[:10],
    }


def preferences_in(text: str) -> list[str]:
    """Frases del cliente sobre sus preferencias, sin datos de pago ni números largos."""
    found: list[str] = []
    for match in _PREFERENCE.finditer(text or ""):
        phrase = " ".join(match.group(0).split()).strip(" ,;:-")
        if phrase and not _PAYMENT.search(phrase):
            found.append(phrase[:MAX_PREFERENCE_CHARS])
    return found


def _merge_purchases(
    known: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {p["pedido"]: p for p in known}
    order = [p["pedido"] for p in known]
    for purchase in new:
        if purchase["pedido"] in by_id:
            order.remove(purchase["pedido"])
        by_id[purchase["pedido"]] = purchase
        order.append(purchase["pedido"])
    return [by_id[i] for i in order][-MAX_ENTRIES:]


def remember(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    customer_key: str,
    *,
    case_id: uuid.UUID,
    case_type: str | None,
    text: str,
    orders: list[dict[str, Any]],
    order_ids: list[str],
    outcome: str,
) -> None:
    """Suma el turno de un cliente verificado a su memoria. `orders`: pedidos que pasaron el
    control de dueño en este turno; `order_ids`: los pedidos que usaron las herramientas."""
    purchases = [p for p in (purchase_from(o) for o in orders) if p is not None]
    names = [str(o["customer_name"]) for o in orders if o.get("customer_name")]
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            f"select {_COLUMNS} from customer_memories where customer_key = %s for update",
            (customer_key,),
        ).fetchone()
        current = (
            _memory(row)
            if row
            else Memory(uuid.uuid4(), "", [], [], [], datetime.now())  # todavía sin fila
        )
        name = names[-1][:MAX_NAME_CHARS] if names else current.name
        preferences = list(current.preferences)
        for phrase in preferences_in(text):
            if phrase.lower() not in (p.lower() for p in preferences):
                preferences.append(phrase)
        preferences = preferences[-MAX_PREFERENCES:]
        all_purchases = _merge_purchases(current.purchases, purchases)
        history = [dict(h) for h in current.history]
        entry = next((h for h in history if h.get("caso") == str(case_id)), None)
        if entry is None:
            entry = {"caso": str(case_id), "tema": "", "pedido": "", "productos": []}
            history.append(entry)
        entry["fecha"] = datetime.now().date().isoformat()
        entry["tema"] = case_type or entry.get("tema") or ""
        entry["resultado"] = outcome
        if order_ids:
            entry["pedido"] = order_ids[-1]
        products = {p["pedido"]: p["productos"] for p in all_purchases}
        if entry.get("pedido") in products:
            entry["productos"] = products[entry["pedido"]]
        history = history[-MAX_ENTRIES:]
        conn.execute(
            "insert into customer_memories (tenant_id, customer_key, name, preferences,"
            " purchases, history) values (%s, %s, %s, %s, %s, %s)"
            " on conflict (tenant_id, customer_key) do update set name = excluded.name,"
            " preferences = excluded.preferences, purchases = excluded.purchases,"
            " history = excluded.history, updated_at = now(), last_seen_at = now()",
            (
                tenant_id,
                customer_key,
                name,
                Jsonb(preferences),
                Jsonb(all_purchases),
                Jsonb(history),
            ),
        )


def correct(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    memory_id: uuid.UUID,
    *,
    name: str,
    preferences: list[str],
) -> bool:
    """El equipo corrige el nombre y las preferencias. Una preferencia con datos de pago no se
    guarda."""
    clean = [
        " ".join(p.split())[:MAX_PREFERENCE_CHARS]
        for p in preferences
        if p.strip() and not _PAYMENT.search(p)
    ][:MAX_PREFERENCES]
    with tenant_scope(conn, tenant_id):
        cursor = conn.execute(
            "update customer_memories set name = %s, preferences = %s, updated_at = now()"
            " where id = %s",
            (" ".join(name.split())[:MAX_NAME_CHARS], Jsonb(clean), memory_id),
        )
    return cursor.rowcount == 1


def forget(conn: psycopg.Connection, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
    with tenant_scope(conn, tenant_id):
        cursor = conn.execute("delete from customer_memories where id = %s", (memory_id,))
    return cursor.rowcount == 1


# -- para el prompt --------------------------------------------------------------------------------
def outcome_text(outcome: str, language: str = "es") -> str:
    return _OUTCOME_TEXT["en" if language == "en" else "es"].get(outcome, outcome)


def _topic(entry: dict[str, Any], language: str) -> str:
    parts = [str(entry.get("tema") or "")]
    if entry.get("pedido"):
        order = "order" if language == "en" else "pedido"
        parts.append(f"{order} {entry['pedido']}")
    if entry.get("productos"):
        parts.append(f"({', '.join(entry['productos'])})")
    return " ".join(p for p in parts if p)


def render(found: Memory, language: str = "es") -> str:
    """El texto de `<memoria_cliente>`: corto, sin claves ni datos de pago."""
    lines: list[str] = []
    if found.name:
        lines.append(f"nombre: {found.name}")
    if found.open_cases:
        lines.append("casos_abiertos:")
        lines += [
            f"- {_topic(h, language)}, {outcome_text(str(h.get('resultado')), language)},"
            f" {h.get('fecha', '')}"
            for h in found.open_cases
        ]
    if found.purchases:
        lines.append("compras:")
        lines += [
            f"- {p['pedido']}: {', '.join(p['productos'])} ({p['estado']}, {p['fecha']})"
            for p in found.purchases
        ]
    if found.preferences:
        lines.append("preferencias:")
        lines += [f"- {p}" for p in found.preferences]
    open_ids = {h.get("caso") for h in found.open_cases}
    past = [h for h in found.history if h.get("caso") not in open_ids]
    if past:
        lines.append("conversaciones_anteriores:")
        lines += [
            f"- {h.get('fecha', '')}: {_topic(h, language)},"
            f" {outcome_text(str(h.get('resultado')), language)}"
            for h in past
        ]
    return "\n".join(lines)
