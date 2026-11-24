"""Nombres legibles para el panel: temas ("Dónde está mi pedido") y acciones de las
acciones ("Reembolso · Pedido 1005 · $35.000")."""

from decimal import Decimal, InvalidOperation
from typing import Any

from app.panel import english

# Etiquetas del clasificador que no son temas de la empresa.
SPECIAL = {
    "conversacion_general": "Conversación general",
    "consulta_general": "Consulta general",
    "derivar_humano": "Pidió hablar con una persona",
    "pedido_no_permitido": "Pedido no permitido",
    "amenaza_legal": "Amenaza de denuncia o acción legal",
    "otro": "Otro tema",
}
# Los nombres de los tipos van sin tildes (son identificadores): se restituyen para mostrarlos.
ACCENTS = {
    "donde": "dónde",
    "esta": "está",
    "como": "cómo",
    "cuando": "cuándo",
    "cuanto": "cuánto",
    "direccion": "dirección",
    "facturacion": "facturación",
    "devolucion": "devolución",
    "cancelacion": "cancelación",
    "informacion": "información",
    "envio": "envío",
    "envios": "envíos",
    "credito": "crédito",
    "debito": "débito",
    "garantia": "garantía",
    "politica": "política",
    "politicas": "políticas",
    "codigo": "código",
    "atencion": "atención",
    "promocion": "promoción",
    "suscripcion": "suscripción",
    "dias": "días",
}


# Acciones de las acciones conocidas; el resto se arma desde el nombre.
ACTIONS = {
    "iniciar_reembolso": "Reembolso",
    "cancelar_pedido": "Cancelación",
    "actualizar_direccion": "Cambio de dirección",
    "consultar_pedido": "Consulta de pedido",
    "cancelar_pendiente": "Cancelación de lo pendiente",  # decisión 040
}
# Verbos con los que suele empezar una acción que crea algo: "crear_nota" -> "Nota".
_LEADING_VERBS = ("iniciar_", "crear_", "generar_", "registrar_", "emitir_")
_AMOUNTS = {"amount", "monto", "importe", "total"}
_ORDERS = {"order_id", "pedido_id", "id_pedido", "numero_de_pedido"}
_PARAMS = {"address": "Dirección", "direccion": "Dirección", "reason": "Motivo", "motivo": "Motivo"}


# Panel en inglés (decisión 037).
SPECIAL_EN = {
    "conversacion_general": "General conversation",
    "consulta_general": "General question",
    "derivar_humano": "Asked to talk to a person",
    "pedido_no_permitido": "Request not allowed",
    "amenaza_legal": "Threat of a complaint or legal action",
    "otro": "Other topic",
}
ACTIONS_EN = {
    "iniciar_reembolso": "Refund",
    "cancelar_pedido": "Cancellation",
    "actualizar_direccion": "Address change",
    "consultar_pedido": "Order lookup",
    "iniciar_cambio": "Exchange",
    "ofrecer_cupon": "Coupon",
    "ofrecer_envio_express": "Express shipping",
    "cancelar_pendiente": "Pending request cancelled",
    "buscar_productos": "Product search",
}
_PARAMS_EN = {
    "query": "Search",
    "size": "Size",
    "address": "Address",
    "direccion": "Address",
    "reason": "Reason",
    "motivo": "Reason",
    "new_size": "New size",
    "percent": "Discount",
    "item_sku": "Item",
    "new_sku": "New item",
    "new_color": "New color",
    "new_product": "New product",
}


def money(value: object) -> str:
    """`35000` -> `$35.000` (pesos, sin centavos). En inglés, `$1,250.50`. Lo que no es un
    número, tal cual."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if english.english():
        return f"${amount:,.2f}".removesuffix(".00")
    return "$" + f"{amount:,.0f}".replace(",", ".")


def tool_label(name: str | None) -> str:
    """Nombre legible de una acción: `iniciar_reembolso` -> `Reembolso`."""
    en = english.english()
    if not name:
        return "Action" if en else "Acción"
    if en and name in ACTIONS_EN:
        return ACTIONS_EN[name]
    if name in ACTIONS and not en:
        return ACTIONS[name]
    for verb in _LEADING_VERBS:
        if name.startswith(verb) and len(name) > len(verb):
            return type_label(name[len(verb) :])
    return type_label(name)


REFUND_TOOLS = {"iniciar_reembolso", "reembolsar", "refund"}


def refund_confirm(tool: str) -> str:
    """Los atributos del botón Aprobar de un reembolso: pide confirmación (decisión 089)."""
    if tool not in REFUND_TOOLS and "reembols" not in tool and "refund" not in tool:
        return ""
    return (
        "data-confirm=\"Approve this refund? The money goes back to the customer and it can't "
        'be undone." data-confirm-ok="Approve refund"'
    )


def action_label(tool: str | None, params: dict[str, Any] | None) -> str:
    """Una acción en palabras: `Reembolso · Pedido 1005 · $35.000`. Texto sin escapar."""
    parts = [tool_label(tool)]

    def rank(item: tuple[str, Any]) -> int:
        # jsonb no conserva el orden de las claves: primero el pedido y al final el monto.
        low = item[0].lower()
        return 0 if low in _ORDERS else 2 if low in _AMOUNTS else 1

    en = english.english()
    for key, value in sorted((params or {}).items(), key=rank):
        low = key.lower()
        if low in _ORDERS:
            parts.append(f"{'Order' if en else 'Pedido'} {value}")
        elif low in _AMOUNTS:
            parts.append(money(value))
        elif en and low == "percent":
            parts.append(f"{value}% off")
        else:
            names = _PARAMS_EN if en else _PARAMS
            parts.append(f"{names.get(low) or type_label(low)}: {value}")
    return " · ".join(parts)


def type_label(name: str | None) -> str:
    en = english.english()
    if not name:
        return "Unclassified" if en else "Sin clasificar"
    if en and name in SPECIAL_EN:
        return SPECIAL_EN[name]
    if en:  # los nombres de una empresa en inglés ya están en inglés: `where_is_my_order`
        text = " ".join(w for w in name.replace("-", "_").split("_") if w)
        return text[:1].upper() + text[1:]
    if name in SPECIAL:
        return SPECIAL[name]
    words = [ACCENTS.get(w, w) for w in name.replace("-", "_").split("_") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]
