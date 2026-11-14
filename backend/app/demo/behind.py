"""Vista "detrás de escena" de la demo (Hito 8, tarea 6; decisión 041).

Arma, desde la auditoría del caso, lo que pasó en cada turno de una conversación del chat web:

- la intención detectada y la verificación;
- las herramientas llamadas, con sus parámetros y las reglas del guardián que se aplicaron;
- las fuentes citadas y la aprobación pendiente;
- la latencia y el costo.

Es solo lectura y sin datos personales:

- **El texto del cliente no se muestra:** puede traer cualquier dato, y ya se ve en el chat.
- **Los parámetros personales** (dirección, email, teléfono, nombre) se reemplazan por "•••".
- **Los resultados de las herramientas no se muestran;** solo su estado y lo que el guardián
  decidió.

Solo ve la conversación de la sesión del navegador que la pide. Se activa por empresa en el
panel (`tenants.settings.behind_the_scenes`) y está apagada por defecto.
"""

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.audit.log import reconstruct_case
from app.channels.web.channel import token_hash
from app.contacts import WEB_PREFIX, hash_address
from app.db.connection import tenant_scope
from app.panel import english
from app.panel.labels import money, tool_label, type_label
from app.policy import guardian as guard

SETTING = "behind_the_scenes"
PERSONAL_KEYS = {
    "address",
    "new_address",
    "shipping_address",
    "direccion",
    "email",
    "phone",
    "customer_phone",
    "telefono",
    "name",
    "customer_name",
    "nombre",
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_CODE = re.compile(r"\b\d{6}\b")
VERIFICATION_STEPS = {
    "verification_requested": "email_requested",
    "verification_code_sent": "code_sent",
    "verification_code_failed": "code_failed",
}
CHECKS = {"grounding_rejected", "format_rejected", "tone_rejected"}


def enabled(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    """Apagada salvo que la empresa la active."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select coalesce((settings ->> %s)::boolean, false) from tenants where id = %s",
            (SETTING, tenant_id),
        ).fetchone()
    return bool(row and row[0])


def set_enabled(conn: psycopg.Connection, tenant_id: uuid.UUID, on: bool) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({SETTING: on}), tenant_id),
        )


def redact_text(text: str) -> str:
    """Emails, teléfonos y códigos de 6 dígitos fuera de un texto (un error de la API)."""
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return _CODE.sub("••••••", text)


def redact_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: "•••" if key.lower() in PERSONAL_KEYS else value
        for key, value in (params or {}).items()
    }


def _say(text: str) -> str:
    """Los motivos internos están en español: en una empresa en inglés, traducidos."""
    return english.PHRASES.get(text, text) if english.english() else text


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


# -- reglas del guardián ---------------------------------------------------------------------
def _limit(manifest: dict[str, Any], value: Any) -> str:
    return f"{value}%" if manifest.get("amount_from") == "input.percent" else money(value)


def rules(manifest: dict[str, Any] | None, status: str, reason: str) -> list[dict[str, Any]]:
    """Las reglas que el guardián controla para esta herramienta y cómo salió cada una: True
    (pasó), False (la frenó) o None (no se llegó a controlar)."""
    en = english.english()
    found: list[dict[str, Any]] = []
    stopped = False

    def add(rule: str, label: str, failed: bool) -> None:
        nonlocal stopped
        ok: bool | None = None if stopped else not failed
        item: dict[str, Any] = {"rule": rule, "label": label, "ok": ok}
        if rule == "approval_over" and failed and not stopped:
            item["hold"] = True  # no la frena: la deja esperando a una persona
        found.append(item)
        stopped = stopped or failed

    add(
        "enabled",
        "Allowed for this case type" if en else "Habilitada para este tipo de caso",
        reason.startswith(("Herramienta no habilitada", "Herramienta inexistente")),
    )
    if manifest is None:
        return found
    add(
        "valid_input",
        "Valid parameters" if en else "Parámetros válidos",
        reason == "Parámetros inválidos.",
    )
    if manifest.get("requires_verified_contact"):
        add(
            "verified_customer",
            "Verified customer" if en else "Cliente verificado",
            reason == guard.UNVERIFIED_REASON,
        )
    if manifest.get("owner_check"):
        add(
            "owner",
            "The order belongs to the customer" if en else "El pedido es del cliente",
            reason == "El recurso no pertenece al contacto.",
        )
    if reason in (guard.CHANGED_MIND_REASON, guard.ALREADY_PENDING_REASON):
        add(
            "pending",
            "Nothing else pending on the same order" if en else "Nada pendiente en el pedido",
            True,
        )
    if manifest.get("max_amount") is not None:
        cap = _limit(manifest, manifest["max_amount"])
        add("cap", f"Up to {cap}" if en else f"Hasta {cap}", reason.startswith("El monto supera"))
    if manifest.get("risk") == "high":
        label = "Always needs a person's approval" if en else "Siempre con aprobación"
        add("approval_over", label, status == "approval_required")
    elif manifest.get("approval_over") is not None:
        over = _limit(manifest, manifest["approval_over"])
        label = f"A person approves over {over}" if en else f"Aprobación sobre {over}"
        add("approval_over", label, status == "approval_required")
    if manifest["upstream"]["method"] != "GET" or status in ("executed", "failed"):
        add(
            "system",
            "Ran in the store's system" if en else "Se ejecutó en el sistema de la empresa",
            status == "failed",
        )
    return found


# -- el caso de la sesión ---------------------------------------------------------------------
def _case(conn: psycopg.Connection, tenant_id: uuid.UUID, session: str) -> tuple[Any, ...] | None:
    contact_hash = hash_address(f"{WEB_PREFIX}{token_hash(session)}")
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            "select c.id, c.status, t.name from cases c join contacts k on k.id = c.contact_id"
            " left join case_types t on t.id = c.case_type_id"
            " where k.phone_hash = %s and c.archived_at is null"
            " order by c.created_at desc limit 1",
            (contact_hash,),
        ).fetchone()


def _manifests(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute("select name, manifest from tools").fetchall()
    return {name: manifest for name, manifest in rows}


def _approvals(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: Any) -> dict[str, str]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, status from approvals where case_id = %s", (case_id,)
        ).fetchall()
    return {str(approval_id): status for approval_id, status in rows}


def _tool(manifests: dict[str, dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("tool") or "")
    status = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    found: dict[str, Any] = {
        "name": name,
        "label": tool_label(name),
        "params": redact_params(payload.get("input")),
        "status": status,
        "reason": _say(reason) if reason else "",
    }
    if name == guard.CANCEL_PENDING:
        cancelled = len((result or {}).get("canceladas") or [])
        found["rules"] = [
            {
                "rule": "own_case",
                "label": "Only this conversation's requests"
                if english.english()
                else "Solo lo pendiente de esta conversación",
                "ok": True,
            }
        ]
        found["cancelled"] = cancelled
        return found
    found["rules"] = rules(manifests.get(name), status, reason)
    if result and result.get("alternativas"):
        found["alternatives"] = [
            {"tool": tool_label(a.get("herramienta")), "max": a.get("maximo")}
            for a in result["alternativas"]
        ]
    if status == "failed" and result and result.get("error"):
        found["error"] = redact_text(str(result["error"]))
    return found


def steps(conn: psycopg.Connection, tenant_id: uuid.UUID, session: str) -> dict[str, Any]:
    """Los turnos de la conversación de la sesión, en orden."""
    case = _case(conn, tenant_id, session)
    if case is None:
        return {"case": None, "turns": []}
    case_id, case_status, case_type = case
    manifests = _manifests(conn, tenant_id)
    approvals = _approvals(conn, tenant_id, case_id)
    turns: list[dict[str, Any]] = []
    for event in reconstruct_case(conn, tenant_id, case_id):
        kind, p = event.kind, event.payload
        if kind == "message_received":
            turns.append(
                {
                    "at": event.created_at,
                    "verified": bool(p.get("verified")),
                    "intent": None,
                    "second_topic": None,
                    "verification": [],
                    "searched": [],
                    "tools": [],
                    "checks": [],
                    "sources": [],
                    "action": None,
                    "approval": None,
                    "latency_ms": 0,
                    "cost_usd": 0.0,
                    "tokens": 0,
                    "model_calls": 0,
                }
            )
            continue
        if not turns:
            continue
        turn = turns[-1]
        if kind == "classified":
            name = p.get("case_type")
            turn["intent"] = {"name": name, "label": type_label(name)}
        elif kind == "second_topic":
            name = p.get("case_type")
            turn["second_topic"] = {"name": name, "label": type_label(name)}
        elif kind in VERIFICATION_STEPS:
            turn["verification"].append({"kind": VERIFICATION_STEPS[kind]})
        elif kind == "verification_checked":
            result = str(p.get("result") or "")
            turn["verification"].append({"kind": result})
            if result == "verified":
                turn["verified"] = True
        elif kind == "rag_search":
            turn["searched"] = sorted(set(p.get("sources") or []))
        elif kind == "tool_call":
            tool = _tool(manifests, p)
            turn["tools"].append(tool)
            if p.get("approval_id"):
                status = approvals.get(str(p["approval_id"]), "pending")
                turn["approval"] = {"status": status}
                if status == "approved":  # una persona aprobó: el guardián la ejecutó
                    for rule in tool["rules"]:
                        if rule["rule"] == "system":
                            rule["ok"] = True
        elif kind in CHECKS:
            turn["checks"].append({"kind": kind, "reason": _say(str(p.get("reason") or ""))})
        elif kind == "final_action":
            turn["action"] = p.get("action")
            turn["sources"] = list(p.get("sources") or [])
            if p.get("approval_id") and turn["approval"] is None:
                turn["approval"] = {"status": approvals.get(str(p["approval_id"]), "pending")}
        elif kind == "escalated":
            turn["action"] = "derivar"
            turn["handoff"] = _say(str(p.get("motivo") or ""))
        elif kind == "model_call":
            turn["cost_usd"] = float(_number(turn["cost_usd"]) + _number(p.get("cost_usd")))
            turn["tokens"] += int(p.get("input_tokens") or 0) + int(p.get("output_tokens") or 0)
            turn["model_calls"] += 1
        elif kind == "turn_timing":
            turn["latency_ms"] = int(p.get("total_ms") or 0)
    return {
        "case": {
            "status": case_status,
            "type": {"name": case_type, "label": type_label(case_type)} if case_type else None,
        },
        "turns": turns,
    }
