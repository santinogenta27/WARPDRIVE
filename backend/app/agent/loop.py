"""Ciclo del agente por mensaje entrante (spec: "Agente en producción").

Pasos: caso abierto o nuevo → clasificación (Haiku; una inyección o un pedido de datos en masa
recibe una negativa fija) → tipo activo → verificación de identidad
→ RAG → ciclo de herramientas con Sonnet (máximo 8 pasos) → una de cuatro acciones:
responder, ejecutar, pedir_aprobacion o derivar.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
import yaml

from app.agent import prompt
from app.agent.classifier import ASK_FOR_HUMAN, NOT_ALLOWED, classify
from app.agent.grounding import GroundingVerdict, check_grounding
from app.audit import log as audit_log
from app.db.connection import tenant_scope
from app.llm.client import LLMClient
from app.llm.embedder import Embedder
from app.llm.pricing import cost_usd
from app.llm.types import LLMResponse
from app.policy.guardian import ContactContext, GuardDecision, Guardian
from app.rag.search import SearchResult, hybrid_search

AGENT_MODEL = "claude-sonnet-5-5"
MAX_STEPS = 8
Action = Literal["responder", "ejecutar", "pedir_aprobacion", "derivar"]

VERIFICATION_MESSAGE = (
    "Para darte datos de tu pedido necesito verificar que sos el titular. "
    "¿Me pasás el email con el que hiciste la compra? Te mando un código para confirmarlo."
)
ESCALATION_MESSAGE = (
    "Te paso con una persona del equipo, que te va a responder por este mismo chat."
)
REFUSAL_MESSAGE = (
    "Perdón, con eso no puedo ayudarte. Sí puedo darte una mano con tus propios pedidos o con "
    "cualquier consulta sobre la tienda."
)


@dataclass
class ToolExecution:
    name: str
    input: dict[str, Any]
    status: str
    reason: str = ""


@dataclass
class AgentResult:
    case_id: uuid.UUID
    action: Action
    message: str
    case_type: str | None
    tool_calls: list[ToolExecution] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    verification_requested: bool = False
    escalation_summary: dict[str, Any] | None = None
    approval_id: uuid.UUID | None = None


@dataclass
class _Turn:
    """Estado de un mensaje en curso."""

    tenant_id: uuid.UUID
    case_id: uuid.UUID
    contact: ContactContext
    text: str
    case_type: str | None = None
    tool_calls: list[ToolExecution] = field(default_factory=list)
    decisions: list[GuardDecision] = field(default_factory=list)
    documents: list[SearchResult] = field(default_factory=list)
    grounding_retries: int = 0


def _grounding_correction(tool_use_id: str | None, reason: str) -> dict[str, Any]:
    """Devuelve al modelo la respuesta rechazada para que la reescriba (una sola vez)."""
    text = (
        f"Respuesta rechazada por el control de fundamento: {reason}. Usá solo datos de "
        "<documentos> citando su id en fuentes, o resultados de herramientas; si no alcanza, "
        "derivá."
    )
    if tool_use_id is None:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps({"error": text}, ensure_ascii=False),
                "is_error": True,
            }
        ],
    }


class Agent:
    def __init__(
        self,
        conn: psycopg.Connection,
        llm: LLMClient,
        embedder: Embedder,
        guardian: Guardian,
        *,
        model: str = AGENT_MODEL,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self._conn = conn
        self._llm = llm
        self._embedder = embedder
        self._guardian = guardian
        self._model = model
        self._max_steps = max_steps

    # -- entrada ---------------------------------------------------------------------------
    def handle_message(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> AgentResult:
        company, tone = self._tenant(tenant_id)
        contact = self._contact(tenant_id, contact_id)
        case_id = self._open_case(tenant_id, contact_id)
        self._save_message(tenant_id, case_id, "customer", text)
        turn = _Turn(tenant_id=tenant_id, case_id=case_id, contact=contact, text=text)
        self._audit(turn, "message_received", {"text": text, "verified": contact.verified})

        case_types = self._active_case_types(tenant_id)
        classification = classify(
            self._llm,
            tenant_id=tenant_id,
            case_id=case_id,
            company=company,
            message=text,
            case_types={name: goal for name, (goal, _) in case_types.items()},
        )
        self._audit_model_call(turn, "clasificador", classification.response)
        label = classification.label
        self._audit(turn, "classified", {"case_type": label})
        if label == ASK_FOR_HUMAN:
            return self._escalate(turn, "El cliente pidió hablar con una persona.")
        if label == NOT_ALLOWED:  # inyección o datos en masa: negativa fija, sin modelo ni derivar
            return self._finish(turn, "responder", REFUSAL_MESSAGE)
        if label not in case_types:
            return self._escalate(turn, "El tipo de caso no está activo para la empresa.")
        turn.case_type = label
        self._set_case_type(tenant_id, case_id, label)

        instructions = case_types[label][1]
        tool_names = list(yaml.safe_load(instructions).get("herramientas") or [])
        manifests = self._guardian.manifests(tenant_id, tool_names)
        if not contact.verified and any(m["requires_verified_contact"] for m in manifests):
            return self._finish(turn, "responder", VERIFICATION_MESSAGE, verification=True)

        turn.documents = hybrid_search(self._conn, tenant_id, self._embedder, text, k=5)
        self._audit(
            turn,
            "rag_search",
            {
                "query": text,
                "sources": [d.source for d in turn.documents],
                "chunk_ids": [str(d.chunk_id) for d in turn.documents],
            },
        )
        system = prompt.system_prompt(company, tone, instructions)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": prompt.user_turn(
                    turn.documents, contact.verified, self._history(tenant_id, case_id), text
                ),
            }
        ]
        return self._run_loop(turn, system, messages, manifests)

    # -- ciclo de herramientas -------------------------------------------------------------
    def _run_loop(
        self,
        turn: _Turn,
        system: str,
        messages: list[dict[str, Any]],
        manifests: list[dict[str, Any]],
    ) -> AgentResult:
        tools = prompt.tool_definitions(manifests)
        allowed = {m["name"] for m in manifests}
        for _ in range(self._max_steps):
            response = self._llm.complete(
                tenant_id=turn.tenant_id,
                case_id=turn.case_id,
                model=self._model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=4096,
                output_config={"effort": "low"},
            )
            self._audit_model_call(turn, "agente", response)
            if response.stop_reason == "refusal":
                return self._escalate(turn, "El modelo no pudo responder este pedido.")
            final = next(
                (c for c in response.tool_calls if c.name in prompt.FINAL_TOOL_NAMES), None
            )
            business = [c for c in response.tool_calls if c.name not in prompt.FINAL_TOOL_NAMES]

            if not response.tool_calls:  # texto suelto: se trata como respuesta sin fuentes
                final_name, final_input, final_id = (
                    "responder",
                    {"mensaje": response.text, "fuentes": [], "factual": True},
                    None,
                )
            elif final is not None and not business:
                final_name, final_input, final_id = final.name, final.input, final.id
            else:
                final_name = ""
            if final_name:
                if final_name == "responder":
                    verdict = self._check_grounding(turn, final_input)
                    if not verdict.ok:
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {"reason": verdict.reason, "rejected_message": final_input},
                        )
                        if turn.grounding_retries >= 1:
                            return self._escalate(turn, f"Control de fundamento: {verdict.reason}.")
                        turn.grounding_retries += 1
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(_grounding_correction(final_id, verdict.reason))
                        continue
                return self._final_action(turn, final_name, final_input)

            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                if call.name in prompt.FINAL_TOOL_NAMES:
                    outcome: dict[str, Any] = {
                        "error": "Primero se ejecutan las herramientas; respondé después."
                    }
                else:
                    decision = self._guardian.execute(
                        tenant_id=turn.tenant_id,
                        case_id=turn.case_id,
                        contact=turn.contact,
                        tool_name=call.name,
                        tool_input=call.input,
                        allowed=allowed,
                    )
                    turn.decisions.append(decision)
                    turn.tool_calls.append(
                        ToolExecution(call.name, call.input, decision.status, decision.reason)
                    )
                    outcome = decision.result
                    self._audit(
                        turn,
                        "tool_call",
                        {
                            "tool": call.name,
                            "input": call.input,
                            "status": decision.status,
                            "reason": decision.reason,
                            "result": outcome,
                            "approval_id": str(decision.approval_id)
                            if decision.approval_id
                            else None,
                        },
                    )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(outcome, ensure_ascii=False, default=str),
                        **({"is_error": True} if "error" in outcome else {}),
                    }
                )
            messages.append({"role": "user", "content": results})
        return self._escalate(turn, f"Se alcanzó el límite de {self._max_steps} pasos.")

    def _check_grounding(self, turn: _Turn, data: dict[str, Any]) -> GroundingVerdict:
        documents: dict[str, str] = {}
        for doc in turn.documents:
            documents[doc.source] = f"{documents.get(doc.source, '')}\n{doc.text}"
        evidence = [turn.text] + [json.dumps(d.result, ensure_ascii=False) for d in turn.decisions]
        return check_grounding(
            str(data.get("mensaje") or ""),
            [str(s) for s in data.get("fuentes") or []],
            bool(data.get("factual", True)),
            documents,
            evidence,
            tool_backed=bool(turn.decisions),
        )

    def _final_action(self, turn: _Turn, name: str, data: dict[str, Any]) -> AgentResult:
        if name == "derivar":
            return self._escalate(turn, str(data.get("motivo") or "Derivado por el agente."))
        message = str(data.get("mensaje") or "").strip()
        sources = [str(s) for s in data.get("fuentes") or []]
        pending = next((d for d in turn.decisions if d.status == "approval_required"), None)
        if pending is not None:
            return self._finish(
                turn, "pedir_aprobacion", message, sources=sources, approval_id=pending.approval_id
            )
        action: Action = (
            "ejecutar"
            if any(d.status == "executed" and d.side_effect for d in turn.decisions)
            else "responder"
        )
        return self._finish(turn, action, message, sources=sources)

    # -- cierre ----------------------------------------------------------------------------
    def _escalate(self, turn: _Turn, reason: str) -> AgentResult:
        summary = {
            "intencion": turn.case_type or "desconocida",
            "mensaje_cliente": turn.text,
            "contacto_verificado": turn.contact.verified,
            "pasos": [
                {"herramienta": t.name, "parametros": t.input, "resultado": t.status}
                for t in turn.tool_calls
            ],
            "motivo": reason,
        }
        with tenant_scope(self._conn, turn.tenant_id):
            self._conn.execute(
                "update cases set status = 'escalated', escalation = %s, updated_at = now()"
                " where id = %s",
                (json.dumps(summary, ensure_ascii=False), turn.case_id),
            )
        self._audit(turn, "escalated", summary)
        return self._finish(turn, "derivar", ESCALATION_MESSAGE, summary=summary)

    def _finish(
        self,
        turn: _Turn,
        action: Action,
        message: str,
        *,
        sources: list[str] | None = None,
        verification: bool = False,
        summary: dict[str, Any] | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> AgentResult:
        self._save_message(turn.tenant_id, turn.case_id, "agent", message)
        self._audit(
            turn,
            "final_action",
            {
                "action": action,
                "message": message,
                "sources": sources or [],
                "verification_requested": verification,
                "approval_id": str(approval_id) if approval_id else None,
            },
        )
        return AgentResult(
            case_id=turn.case_id,
            action=action,
            message=message,
            case_type=turn.case_type,
            tool_calls=turn.tool_calls,
            sources=sources or [],
            verification_requested=verification,
            escalation_summary=summary,
            approval_id=approval_id,
        )

    # -- auditoría -------------------------------------------------------------------------
    def _audit(self, turn: _Turn, kind: str, payload: dict[str, Any]) -> None:
        audit_log.record(self._conn, turn.tenant_id, turn.case_id, kind, payload)

    def _audit_model_call(self, turn: _Turn, purpose: str, response: LLMResponse) -> None:
        u = response.usage
        self._audit(
            turn,
            "model_call",
            {
                "purpose": purpose,
                "model": response.model,
                "stop_reason": response.stop_reason,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_creation_input_tokens": u.cache_creation_input_tokens,
                "cache_read_input_tokens": u.cache_read_input_tokens,
                "cost_usd": str(cost_usd(response.model, u)),
            },
        )

    # -- datos -----------------------------------------------------------------------------
    def _tenant(self, tenant_id: uuid.UUID) -> tuple[str, str]:
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select name, settings from tenants where id = %s", (tenant_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Tenant inexistente")
        return row[0], (row[1] or {}).get("tone", "rioplatense")

    def _contact(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> ContactContext:
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select id, phone_hash, coalesce(verified_until > now(), false)"
                " from contacts where id = %s",
                (contact_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Contacto inexistente")
        return ContactContext(id=row[0], phone_hash=row[1], verified=row[2])

    def _open_case(self, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> uuid.UUID:
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select id from cases where contact_id = %s and status <> 'resolved'"
                " order by created_at desc limit 1",
                (contact_id,),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
                    (tenant_id, contact_id),
                ).fetchone()
        assert row is not None
        case_id: uuid.UUID = row[0]
        return case_id

    def _active_case_types(self, tenant_id: uuid.UUID) -> dict[str, tuple[str, str]]:
        """nombre -> (objetivo, instrucciones YAML) de los tipos activos."""
        with tenant_scope(self._conn, tenant_id):
            rows = self._conn.execute(
                "select name, instructions from case_types where active order by name"
            ).fetchall()
        result: dict[str, tuple[str, str]] = {}
        for name, instructions in rows:
            parsed = yaml.safe_load(instructions or "") or {}
            result[name] = (str(parsed.get("objetivo", name)), instructions or "")
        return result

    def _set_case_type(self, tenant_id: uuid.UUID, case_id: uuid.UUID, name: str) -> None:
        with tenant_scope(self._conn, tenant_id):
            self._conn.execute(
                "update cases set case_type_id = (select id from case_types where name = %s),"
                " updated_at = now() where id = %s",
                (name, case_id),
            )

    def _save_message(self, tenant_id: uuid.UUID, case_id: uuid.UUID, role: str, text: str) -> None:
        with tenant_scope(self._conn, tenant_id):
            self._conn.execute(
                "insert into case_messages (tenant_id, case_id, role, text)"
                " values (%s, %s, %s, %s)",
                (tenant_id, case_id, role, text),
            )

    def _history(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[tuple[str, str]]:
        """Últimos 20 mensajes previos al actual (el actual va en <mensaje_cliente>)."""
        with tenant_scope(self._conn, tenant_id):
            rows = self._conn.execute(
                "select role, text from case_messages where case_id = %s"
                " order by created_at desc, id desc limit 21",
                (case_id,),
            ).fetchall()
        labels = {"customer": "cliente", "agent": "asistente", "human": "equipo"}
        return [(labels[r[0]], r[1]) for r in reversed(rows[1:])]
