"""Guardián de políticas: ninguna herramienta se ejecuta sin pasar por acá.

Los permisos se aplican en código, nunca en el prompt. Orden de controles:
  1. La herramienta está habilitada para el tipo de caso y aprobada por el cliente.
  2. La entrada valida contra el esquema estricto del manifiesto.
  3. Identidad: si el manifiesto lo pide, el contacto está verificado.
  4. Dueño: el recurso (p. ej. el pedido) pertenece al contacto. Si no existe o es de otro, la
     respuesta es la misma, para no revelar que existe.
  5. Monto y riesgo: sobre `max_amount` se rechaza; riesgo alto o monto sobre `approval_over`
     crea una aprobación humana y no se ejecuta.
  6. Ejecución contra la API del cliente.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.tools.runtime import ToolRuntime, UpstreamResult, idempotency_key

DecisionStatus = Literal["executed", "denied", "approval_required", "failed"]
NOT_FOUND_FOR_CONTACT = "No hay un recurso con ese identificador entre los del contacto."
UNVERIFIED_REASON = "El contacto no está verificado."  # el agente pide la verificación
ALREADY_PENDING_REASON = "Ya hay una solicitud pendiente de aprobación para el mismo recurso."
_HIDDEN_FIELDS = {"customer_phone"}  # el modelo no necesita el teléfono en claro


def _resource_key(manifest: dict[str, Any], tool_input: dict[str, Any]) -> str | None:
    """Recurso sobre el que actúa la herramienta: los campos de la clave de idempotencia del
    manifiesto, sin el caso (`"case_id + order_id"` -> `"order_id=1005"`)."""
    names = [part.strip() for part in str(manifest.get("idempotency_key") or "").split("+")]
    fields = [n for n in names if n and n != "case_id" and n in tool_input]
    return "&".join(f"{n}={tool_input[n]}" for n in fields) if fields else None


@dataclass(frozen=True)
class ContactContext:
    id: uuid.UUID
    phone_hash: str  # del cliente verificado por email si lo hay; si no, del contacto
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


def _amount(
    manifest: dict[str, Any], tool_input: dict[str, Any], resources: dict[str, Any]
) -> float | None:
    source = manifest.get("amount_from", "input.amount")
    scope, _, name = source.partition(".")
    value = tool_input.get(name) if scope == "input" else (resources.get(scope) or {}).get(name)
    return float(value) if isinstance(value, int | float) else None


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

    def customer_phone(self, tenant_id: uuid.UUID, email: str) -> str | None:
        """Teléfono del cliente con ese email en el sistema de la empresa, para verificar la
        identidad por código. Usa `settings.identity_lookup` del tenant; None si no está
        configurado, si no hay un único cliente o si la API falla."""
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select settings -> 'identity_lookup' from tenants where id = %s", (tenant_id,)
            ).fetchone()
        lookup = row[0] if row else None
        if not isinstance(lookup, dict):
            return None
        try:
            found = self._runtime.call(
                lookup["method"], lookup["path"], {lookup.get("email_param", "email"): email}
            )
        except RuntimeError:
            return None
        if not found.ok or not isinstance(found.data, dict):
            return None
        matches = found.data.get(lookup.get("list_field", "customers")) or []
        phones = [m.get(lookup.get("phone_field", "phone")) for m in matches]
        return phones[0] if len(phones) == 1 and isinstance(phones[0], str) else None

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
        checked = self._precheck(tenant_id, contact, tool_name, tool_input, allowed)
        if isinstance(checked, GuardDecision):
            return checked
        manifest, resources = checked
        decision = self._apply_risk_policy(
            tenant_id=tenant_id,
            case_id=case_id,
            manifest=manifest,
            tool_input=tool_input,
            resources=resources,
            approved_by_human=False,
        )
        if decision is not None:
            return decision
        return self._run(manifest, tool_input, case_id, resources)

    def execute_approved(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        contact: ContactContext,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> GuardDecision:
        """Ejecuta una acción ya aprobada por un humano: repite todos los controles salvo la
        aprobación (la herramienta pudo desaprobarse o el pedido cambiar de dueño o estado)."""
        checked = self._precheck(tenant_id, contact, tool_name, tool_input, {tool_name})
        if isinstance(checked, GuardDecision):
            return checked
        manifest, resources = checked
        decision = self._apply_risk_policy(
            tenant_id=tenant_id,
            case_id=case_id,
            manifest=manifest,
            tool_input=tool_input,
            resources=resources,
            approved_by_human=True,
        )
        if decision is not None:
            return decision
        return self._run(manifest, tool_input, case_id, resources)

    def _precheck(
        self,
        tenant_id: uuid.UUID,
        contact: ContactContext,
        tool_name: str,
        tool_input: dict[str, Any],
        allowed: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]] | GuardDecision:
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
            return _denied(UNVERIFIED_REASON)

        resources: dict[str, Any] = {}
        if manifest.get("owner_check"):
            owner = self._check_owner(manifest, tool_input, contact)
            if owner is None:
                return _denied("El recurso no pertenece al contacto.", NOT_FOUND_FOR_CONTACT)
            resources.update(owner)
        return manifest, resources

    def _apply_risk_policy(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        manifest: dict[str, Any],
        tool_input: dict[str, Any],
        resources: dict[str, Any],
        approved_by_human: bool,
    ) -> GuardDecision | None:
        """Riesgo bajo: se ejecuta. Riesgo alto o monto sobre `approval_over`: aprobación humana.
        Monto sobre `max_amount`: se rechaza siempre, aun con aprobación."""
        amount = _amount(manifest, tool_input, resources)
        max_amount = manifest.get("max_amount")
        if max_amount is not None and amount is not None and amount > max_amount:
            return _denied("El monto supera el máximo permitido para esta herramienta.")
        if approved_by_human:
            return None
        approval_over = manifest.get("approval_over")
        needs_approval = manifest["risk"] == "high" or (
            approval_over is not None and amount is not None and amount > approval_over
        )
        if not needs_approval:
            return None
        approval_id = self._request_approval(tenant_id, case_id, manifest, tool_input)
        if approval_id is None:
            return GuardDecision(
                "denied",
                {
                    "status": "already_pending",
                    "mensaje": "Ya hay una solicitud igual en revisión. No se creó otra.",
                },
                reason=ALREADY_PENDING_REASON,
                resources=resources,
            )
        return GuardDecision(
            "approval_required",
            {
                "status": "approval_required",
                "mensaje": "La acción quedó pendiente de aprobación de una persona del equipo.",
            },
            reason="Requiere aprobación humana.",
            side_effect=True,
            approval_id=approval_id,
            resources=resources,
        )

    def _request_approval(
        self,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        manifest: dict[str, Any],
        params: dict[str, Any],
    ) -> uuid.UUID | None:
        """Crea la aprobación pendiente y pausa el caso. Una sola pendiente por recurso (el
        pedido): si ya hay una en este caso, se reusa; si está en otro caso, devuelve None y no
        crea nada. Sin clave de recurso, se reusa la idéntica del mismo caso."""
        tool_name = manifest["name"]
        key = _resource_key(manifest, params)
        with tenant_scope(self._conn, tenant_id):
            if key is not None:
                # Dos conversaciones a la vez por el mismo pedido: de a una (y el índice único
                # parcial de la migración 0014 lo garantiza en la base).
                self._conn.execute(
                    "select pg_advisory_xact_lock(hashtext(%s))",
                    (f"{tenant_id}:{tool_name}:{key}",),
                )
                row = self._conn.execute(
                    "select a.id, a.case_id from approvals a join tools t on t.id = a.tool_id"
                    " where t.name = %s and a.resource_key = %s and a.status = 'pending'",
                    (tool_name, key),
                ).fetchone()
                if row is not None and row[1] != case_id:
                    return None
            else:
                row = self._conn.execute(
                    "select a.id from approvals a join tools t on t.id = a.tool_id where"
                    " a.case_id = %s and t.name = %s and a.params = %s and a.status = 'pending'",
                    (case_id, tool_name, Jsonb(params)),
                ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "insert into approvals (tenant_id, case_id, tool_id, params, resource_key)"
                    " select %s, %s, id, %s, %s from tools where name = %s returning id",
                    (tenant_id, case_id, Jsonb(params), key, tool_name),
                ).fetchone()
            self._conn.execute(
                "update cases set status = 'waiting_approval', updated_at = now() where id = %s",
                (case_id,),
            )
        assert row is not None
        approval_id: uuid.UUID = row[0]
        return approval_id

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
