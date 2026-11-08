"""Guardián de políticas: ninguna herramienta se ejecuta sin pasar por acá.

Los permisos se aplican en código, nunca en el prompt. Orden de controles:
  1. La herramienta está habilitada para el tipo de caso y aprobada por el cliente.
  2. La entrada valida contra el esquema estricto del manifiesto.
  3. Identidad: si el manifiesto lo pide, el contacto está verificado.
  4. Dueño: el recurso (p. ej. el pedido) pertenece al contacto. Si no existe o es de otro, la
     respuesta es la misma, para no revelar que existe.
  5. Ejecución contra la API del cliente.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from jsonschema import Draft202012Validator

from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.tools.runtime import ToolRuntime, UpstreamResult, idempotency_key

DecisionStatus = Literal["executed", "denied", "approval_required", "failed"]
NOT_FOUND_FOR_CONTACT = "No hay un recurso con ese identificador entre los del contacto."
_HIDDEN_FIELDS = {"customer_phone"}  # el modelo no necesita el teléfono en claro


@dataclass(frozen=True)
class ContactContext:
    id: uuid.UUID
    phone_hash: str
    verified: bool


@dataclass
class GuardDecision:
    status: DecisionStatus
    result: dict[str, Any]  # lo que ve el modelo como resultado de la herramienta
    reason: str = ""
    side_effect: bool = False  # la herramienta modifica datos del cliente
    approval_id: uuid.UUID | None = None
    resources: dict[str, Any] = field(default_factory=dict)


def _sanitize(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _sanitize(v) for k, v in data.items() if k not in _HIDDEN_FIELDS}
    if isinstance(data, list):
        return [_sanitize(v) for v in data]
    return data


def _denied(reason: str, message: str | None = None) -> GuardDecision:
    return GuardDecision("denied", {"error": message or reason}, reason=reason)


class Guardian:
    def __init__(self, conn: psycopg.Connection, runtime: ToolRuntime) -> None:
        self._conn = conn
        self._runtime = runtime

    def manifests(self, tenant_id: uuid.UUID, names: list[str]) -> list[dict[str, Any]]:
        """Manifiestos aprobados del tenant, en el orden pedido."""
        if not names:
            return []
        with tenant_scope(self._conn, tenant_id):
            rows = self._conn.execute(
                "select name, manifest from tools where approved and name = any(%s)", (names,)
            ).fetchall()
        found = {row[0]: row[1] for row in rows}
        return [found[n] for n in names if n in found]

    def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        contact: ContactContext,
        tool_name: str,
        tool_input: dict[str, Any],
        allowed: set[str],
    ) -> GuardDecision:
        if tool_name not in allowed:
            return _denied("Herramienta no habilitada para este tipo de caso.")
        found = self.manifests(tenant_id, [tool_name])
        if not found:
            return _denied("Herramienta inexistente o no aprobada por el cliente.")
        manifest = found[0]

        errors = sorted(
            Draft202012Validator(manifest["input_schema"]).iter_errors(tool_input), key=str
        )
        if errors:
            return _denied("Parámetros inválidos.", f"Parámetros inválidos: {errors[0].message}")

        if manifest["requires_verified_contact"] and not contact.verified:
            return _denied("El contacto no está verificado.")

        resources: dict[str, Any] = {}
        if manifest.get("owner_check"):
            owner = self._check_owner(manifest, tool_input, contact)
            if owner is None:
                return _denied("El recurso no pertenece al contacto.", NOT_FOUND_FOR_CONTACT)
            resources.update(owner)

        decision = self._apply_risk_policy(
            tenant_id=tenant_id,
            case_id=case_id,
            manifest=manifest,
            tool_input=tool_input,
            resources=resources,
        )
        if decision is not None:
            return decision
        return self._run(manifest, tool_input, case_id, resources)

    # El control de riesgo, montos y aprobaciones se agrega en la tarea 8.
    def _apply_risk_policy(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        manifest: dict[str, Any],
        tool_input: dict[str, Any],
        resources: dict[str, Any],
    ) -> GuardDecision | None:
        return None

    def _check_owner(
        self, manifest: dict[str, Any], tool_input: dict[str, Any], contact: ContactContext
    ) -> dict[str, Any] | None:
        left, _, right = (part.strip() for part in manifest["owner_check"].partition("=="))
        resource_name, _, field_name = left.partition(".")
        if right != "contact.phone" or not field_name or "owner_lookup" not in manifest:
            return None  # expresión no soportada: se niega por seguridad
        lookup = manifest["owner_lookup"]
        found = self._runtime.call(lookup["method"], lookup["path"], tool_input)
        if not found.ok or not isinstance(found.data, dict):
            return None
        owner_value = found.data.get(field_name)
        if not isinstance(owner_value, str) or hash_phone(owner_value) != contact.phone_hash:
            return None
        return {resource_name: found.data}

    def _run(
        self,
        manifest: dict[str, Any],
        tool_input: dict[str, Any],
        case_id: uuid.UUID,
        resources: dict[str, Any],
    ) -> GuardDecision:
        upstream = manifest["upstream"]
        key = idempotency_key(manifest.get("idempotency_key"), {"case_id": case_id, **tool_input})
        side_effect = upstream["method"] != "GET"
        try:
            outcome: UpstreamResult = self._runtime.call(
                upstream["method"], upstream["path"], tool_input, key=key
            )
        except RuntimeError as exc:
            return GuardDecision(
                "failed", {"error": "La API no respondió."}, str(exc), side_effect=side_effect
            )
        if outcome.ok:
            return GuardDecision(
                "executed",
                {"ok": True, "data": _sanitize(outcome.data)},
                side_effect=side_effect,
                resources=resources,
            )
        message = outcome.data.get("message") if isinstance(outcome.data, dict) else None
        return GuardDecision(
            "failed",
            {"error": message or f"La API respondió {outcome.status_code}."},
            reason=f"upstream {outcome.status_code}",
            side_effect=side_effect,
            resources=resources,
        )
