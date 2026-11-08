"""Esquema del manifiesto de herramienta (contrato de la spec, sección "Contratos").

Agregados mínimos a la spec, documentados en docs/decisions/005:
  * `amount_from`: de dónde sale el monto que evalúa el guardián ("input.amount" por defecto, u
    "order.total" cuando la acción no recibe monto, como cancelar un pedido).
  * El cuerpo de la llamada upstream son los campos de entrada que no van en la ruta.
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
        "amount_from": {"enum": ["input.amount", "order.total"]},
        "owner_check": {"type": ["string", "null"]},
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
