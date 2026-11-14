"""Esquema del manifiesto de herramienta (contrato de la spec, sección "Contratos").

Agregados mínimos a la spec, documentados en docs/decisions/005:
  * `amount_from`: de dónde sale el monto que evalúa el guardián ("input.amount" por defecto, u
    "order.total" cuando la acción no recibe monto, como cancelar un pedido; "input.percent"
    para un descuento, decisión 039).
  * `alternatives`: lo que la política permite cuando la acción supera su máximo (decisión 039).
  * El cuerpo de la llamada upstream son los campos de entrada que no van en la ruta.
  * `owner_lookup`: cómo traer el recurso que nombra `owner_check` (p. ej. `order`) para
    comparar su dueño con el contacto antes de ejecutar.
"""

from typing import Any

MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "description": {"type": "string", "minLength": 1},
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"const": "object"},
                "additionalProperties": {"const": False},
                "required": {"type": "array"},
                "properties": {"type": "object"},
            },
            "required": ["type", "properties", "required", "additionalProperties"],
        },
        "risk": {"enum": ["low", "medium", "high"]},
        "requires_verified_contact": {"type": "boolean"},
        "max_amount": {"type": ["number", "null"]},
        "approval_over": {"type": ["number", "null"]},
        "amount_from": {"enum": ["input.amount", "order.total", "input.percent"]},
        # Herramientas que la política permite en lugar de una acción sobre el máximo: el
        # rechazo del guardián las ofrece (decisión 039).
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "owner_check": {"type": ["string", "null"]},
        "owner_lookup": {
            "type": "object",
            "properties": {
                "method": {"const": "GET"},
                "path": {"type": "string", "pattern": "^/"},
            },
            "required": ["method", "path"],
            "additionalProperties": False,
        },
        "upstream": {
            "type": "object",
            "properties": {
                "method": {"enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "path": {"type": "string", "pattern": "^/"},
            },
            "required": ["method", "path"],
            "additionalProperties": False,
        },
        "idempotency_key": {"type": ["string", "null"]},
    },
    "required": [
        "name",
        "description",
        "input_schema",
        "risk",
        "requires_verified_contact",
        "max_amount",
        "approval_over",
        "owner_check",
        "upstream",
        "idempotency_key",
    ],
    "additionalProperties": False,
}
