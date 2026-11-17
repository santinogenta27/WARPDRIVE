"""Vista "detrás de escena" de la demo (Hito 8, tarea 6; decisión 041).

Arma, desde la auditoría del caso, lo que pasó en cada turno de una conversación del chat web:

- la intención detectada y la verificación;
- las herramientas llamadas, con sus parámetros y las reglas del guardián que se aplicaron;
- las fuentes citadas y la aprobación pendiente;
- la latencia y el costo, con el tiempo de cada paso (decisión 044).

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
from app.cases import refund_confirmation
from app.channels.web.channel import token_hash
from app.contacts import WEB_PREFIX, hash_address
from app.db.connection import tenant_scope
from app.obs import timing
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
CHECKS = {"grounding_rejected", "format_rejected", "tone_rejected", "handoff_rejected"}

# Por qué derivó (decisión 045): una línea por causa, armada solo con textos fijos. El motivo que
# escribe el modelo no se muestra: puede traer cualquier dato del cliente.
_BLOCKED = {
    "en": (
        "Answer blocked by grounding check: ",
        "Answer blocked by format check: ",
    ),
    "es": (
        "Respuesta frenada por el control de fundamento: ",
        "Respuesta frenada por el control de formato: ",
    ),
}
HANDOFF_LINES: dict[str, dict[str, str]] = {
    "en": {
        "pidio_persona": "The customer asked to talk to a person",
        "amenaza_legal": "Legal threat: a person always handles it",
        "tipo_inactivo": "This type of request isn't turned on for the store",
        "no_resuelto": "The customer said their question wasn't resolved",
        "rechazo_modelo": "The model couldn't answer this request",
        "reverificar": "Answer blocked: it asked a verified customer to verify again",
        "accion_sin_herramienta": (
            "Answer blocked by grounding check: it claimed an action no tool performed"
        ),
        "tope_de_gasto": "The company reached its monthly spending cap",
        "limite_pasos": "Reached the step limit without a valid answer",
        "agente": "The agent chose to hand off",
        "agente_reconsiderada": (
            "The agent chose to hand off, even after being asked to answer with the tool's data"
        ),
    },
    "es": {
        "pidio_persona": "El cliente pidió hablar con una persona",
        "amenaza_legal": "Amenaza legal: la atiende siempre una persona",
        "tipo_inactivo": "Ese tipo de consulta no está activo en la empresa",
        "no_resuelto": "El cliente dijo que su consulta no se resolvió",
        "rechazo_modelo": "El modelo no pudo responder este pedido",
        "reverificar": "Respuesta frenada: le pidió verificarse a un cliente ya verificado",
        "accion_sin_herramienta": (
            "Respuesta frenada por el control de fundamento: afirmó una acción que ninguna "
            "herramienta hizo"
        ),
        "tope_de_gasto": "La empresa llegó al tope de gasto del mes",
        "limite_pasos": "Llegó al límite de pasos sin una respuesta válida",
        "agente": "El agente eligió derivar",
        "agente_reconsiderada": (
            "El agente eligió derivar, aun después de que se le pidió responder con los datos de "
            "la herramienta"
        ),
    },
}
GROUNDING_DETAILS = {
    "en": {
        "fuente_inexistente": "it cited a document it wasn't given",
        "sin_fuente": "a factual answer with no source or tool",
        "numero": "a number not in the documents or tool results",
        "fecha": "a date not in the documents or tool results",
    },
    "es": {
        "fuente_inexistente": "citó un documento que no recibió",
        "sin_fuente": "una respuesta factual sin fuente ni herramienta",
        "numero": "un número que no está en los documentos ni en las herramientas",
        "fecha": "una fecha que no está en los documentos ni en las herramientas",
    },
}
FORMAT_DETAILS = {
    "en": {
        "respuesta vacía": "it was empty",
        "JSON": "it had JSON",
        "metadatos": "it had metadata",
        "marcas del modelo": "it had model markers",
        "nombre de herramienta": "it named a tool",
    },
    "es": {
        "respuesta vacía": "estaba vacía",
        "JSON": "tenía JSON",
        "metadatos": "tenía metadatos",
        "marcas del modelo": "tenía marcas del modelo",
        "nombre de herramienta": "nombraba una herramienta",
    },
}


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


def handoff_line(cause: Any, *, english: bool) -> str | None:
    """El motivo de una derivación en una línea, desde la causa que registró el agente. None si
    no hay causa (un caso de antes de la decisión 045) o si no se conoce."""
    if not isinstance(cause, dict):
        return None
    lang = "en" if english else "es"
    kind, detail = str(cause.get("tipo") or ""), str(cause.get("detalle") or "")
    grounding, form = _BLOCKED[lang]
    if kind == "fundamento":
        found = GROUNDING_DETAILS[lang].get(detail)
        return grounding + found if found else None
    if kind == "formato":
        found = FORMAT_DETAILS[lang].get(detail)
        return form + found if found else None
    if kind == "agente" and cause.get("reconsiderada"):
        kind = "agente_reconsiderada"
    return HANDOFF_LINES[lang].get(kind)


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


ROLES = {"clasificador": "classifier", "agente": "agent"}


def _timing(payload: dict[str, Any]) -> dict[str, Any]:
    """En qué se fue el tiempo del turno (decisión 044): cada paso y cada llamada al modelo,
    con su espera por el límite de Groq. Los turnos medidos antes no traen el detalle."""
    found: dict[str, Any] = {
        f"{kind}_ms": int(payload.get(f"{kind}_ms") or 0) for kind in (*timing.STEPS, "otros")
    }
    found["queries"] = int((payload.get("llamadas") or {}).get(timing.BASE) or 0)
    found["model_calls"] = [
        {
            "name": ROLES.get(str(step.get("nombre")), "model"),
            "ms": int(step.get("ms") or 0),
            "wait_ms": int(step.get("espera_ms") or 0),
        }
        for step in payload.get("pasos") or []
        if step.get("paso") == timing.MODEL
    ]
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
    by_approval: dict[str, dict[str, Any]] = {}  # aprobación -> el turno que la pidió
    for event in reconstruct_case(conn, tenant_id, case_id):
        kind, p = event.kind, event.payload
        if kind == refund_confirmation.SENT:  # llega después, al decidir (decisión 046)
            asked = by_approval.get(str(p.get("approval_id")))
            if asked is not None and asked["approval"] is not None:
                asked["approval"]["email_sent"] = True
            continue
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
                by_approval[str(p["approval_id"])] = turn
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
                by_approval[str(p["approval_id"])] = turn
        elif kind == "escalated":
            turn["action"] = "derivar"
            turn["handoff"] = handoff_line(p.get("causa"), english=english.english())
        elif kind == "model_call":
            turn["cost_usd"] = float(_number(turn["cost_usd"]) + _number(p.get("cost_usd")))
            turn["tokens"] += int(p.get("input_tokens") or 0) + int(p.get("output_tokens") or 0)
            turn["model_calls"] += 1
        elif kind == "turn_timing":
            turn["latency_ms"] = int(p.get("total_ms") or 0)
            turn["timing"] = _timing(p)
    return {
        "case": {
            "status": case_status,
            "type": {"name": case_type, "label": type_label(case_type)} if case_type else None,
        },
        "turns": turns,
    }
