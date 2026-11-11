"""Nombres legibles para el panel: tipos de caso ("Dónde está mi pedido") y acciones de las
herramientas ("Reembolso · Pedido 1005 · $35.000")."""

from decimal import Decimal, InvalidOperation
from typing import Any

# Etiquetas del clasificador que no son tipos de caso de la empresa.
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


# Acciones de las herramientas conocidas; el resto se arma desde el nombre.
ACTIONS = {
    "iniciar_reembolso": "Reembolso",
    "cancelar_pedido": "Cancelación",
    "actualizar_direccion": "Cambio de dirección",
    "consultar_pedido": "Consulta de pedido",
}
# Verbos con los que suele empezar una herramienta que crea algo: "crear_nota" -> "Nota".
_LEADING_VERBS = ("iniciar_", "crear_", "generar_", "registrar_", "emitir_")
_AMOUNTS = {"amount", "monto", "importe", "total"}
_ORDERS = {"order_id", "pedido_id", "id_pedido", "numero_de_pedido"}
_PARAMS = {"address": "Dirección", "direccion": "Dirección", "reason": "Motivo", "motivo": "Motivo"}


def money(value: object) -> str:
    """`35000` -> `$35.000` (pesos, sin centavos). Lo que no es un número, tal cual."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    return "$" + f"{amount:,.0f}".replace(",", ".")


def tool_label(name: str | None) -> str:
    """Nombre legible de una herramienta: `iniciar_reembolso` -> `Reembolso`."""
    if not name:
        return "Acción"
    if name in ACTIONS:
        return ACTIONS[name]
    for verb in _LEADING_VERBS:
        if name.startswith(verb) and len(name) > len(verb):
            return type_label(name[len(verb) :])
    return type_label(name)


def action_label(tool: str | None, params: dict[str, Any] | None) -> str:
    """Una acción en palabras: `Reembolso · Pedido 1005 · $35.000`. Texto sin escapar."""
    parts = [tool_label(tool)]

    def rank(item: tuple[str, Any]) -> int:
        # jsonb no conserva el orden de las claves: primero el pedido y al final el monto.
        low = item[0].lower()
        return 0 if low in _ORDERS else 2 if low in _AMOUNTS else 1

    for key, value in sorted((params or {}).items(), key=rank):
        low = key.lower()
        if low in _ORDERS:
            parts.append(f"Pedido {value}")
        elif low in _AMOUNTS:
            parts.append(money(value))
        else:
            parts.append(f"{_PARAMS.get(low) or type_label(low)}: {value}")
    return " · ".join(parts)


def type_label(name: str | None) -> str:
    if not name:
        return "Sin clasificar"
    if name in SPECIAL:
        return SPECIAL[name]
    words = [ACCENTS.get(w, w) for w in name.replace("-", "_").split("_") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]
