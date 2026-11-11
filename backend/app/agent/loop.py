"""Ciclo del agente por mensaje entrante (spec: "Agente en producción").

Pasos: caso abierto o nuevo → verificación por código en curso (email o código del cliente) →
clasificación (Haiku; una inyección o un pedido de datos en masa recibe una negativa fija) →
tipo activo → verificación de identidad → RAG → ciclo de herramientas con Sonnet (máximo 8
pasos) → una de cuatro acciones: responder, ejecutar, pedir_aprobacion o derivar.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import psycopg
import yaml

from app.agent import prompt
from app.agent.classifier import (
    ASK_FOR_HUMAN,
    CHAT,
    GENERAL,
    NOT_ALLOWED,
    Classification,
    classify,
)
from app.agent.grounding import GroundingVerdict, check_grounding
from app.agent.voseo import to_voseo
from app.audit import log as audit_log
from app.cases import billing, closing
from app.channels.whatsapp.format import to_whatsapp
from app.db.connection import tenant_scope
from app.identity import email_code
from app.llm.client import LLMClient
from app.llm.embedder import Embedder
from app.llm.pricing import cost_usd
from app.llm.types import LLMResponse
from app.mailer import ConsoleMailer, Email, Mailer
from app.obs import alerts, timing, tracing
from app.policy.guardian import (
    ALREADY_PENDING_REASON,
    UNVERIFIED_REASON,
    ContactContext,
    GuardDecision,
    Guardian,
)
from app.rag.search import SearchResult, hybrid_search

AGENT_MODEL = "claude-sonnet-5-5"
MAX_STEPS = 8
Action = Literal["responder", "ejecutar", "pedir_aprobacion", "derivar"]

VERIFICATION_MESSAGE = (
    "Para darte datos de tu pedido necesito verificar que sos el titular. "
    "¿Me pasás el email con el que hiciste la compra? Te mando un código para confirmarlo."
)
# Con la aprobación creada, si el modelo no da una respuesta válida (o quiere derivar).
APPROVAL_PENDING_MESSAGE = (
    "Tu pedido quedó registrado y en revisión: una persona del equipo lo tiene que aprobar. "
    "Te avisamos por este mismo chat apenas esté."
)
# El mismo pedido ya tiene una solicitud pendiente desde otra conversación.
DUPLICATE_APPROVAL_MESSAGE = (
    "Ya tenemos una solicitud en revisión para ese pedido, así que no hace falta pedirla de "
    "nuevo. Una persona del equipo la está revisando y te avisamos apenas esté."
)
ESCALATION_MESSAGE = (
    "Te paso con una persona del equipo, que te va a responder por este mismo chat."
)
REFUSAL_MESSAGE = (
    "Perdón, con eso no puedo ayudarte. Sí puedo darte una mano con tus propios pedidos o con "
    "cualquier consulta sobre la tienda."
)
# Verificación por código: misma respuesta exista o no el email, para no revelarlo.
CODE_SENT_MESSAGE = (
    "Si ese email está registrado en una compra, te llega un código de 6 dígitos. "
    "Escribilo acá para confirmar que sos el titular. Vence en 10 minutos."
)
VERIFIED_PREFIX = "¡Listo, ya verifiqué tu identidad! "
CODE_EXPIRED_MESSAGE = "Ese código ya venció. Pasame de nuevo tu email y te mando uno nuevo."
CODE_LOCKED_MESSAGE = (
    "Ese código tampoco es correcto y ya no quedan intentos. Si querés, pasame de nuevo tu "
    "email y te mando un código nuevo."
)


CHAT_INSTRUCTIONS = """nombre: conversacion_general
objetivo: Charlar con el cliente de forma amable y llevarlo a lo que el asistente puede resolver
condiciones:
  - Respondé en una o dos frases, cálido y breve, sin derivar.
  - Si saluda, agradece o se despide, respondé el saludo y ofrecé ayuda.
  - Si pregunta si sos un bot o con quién habla, decí que sos el asistente virtual de {empresa}.
  - Si pregunta qué podés hacer, contá en qué podés ayudar ({capacidades}).
  - Si pregunta algo ajeno a la tienda, decí con amabilidad que con eso no podés ayudar
    y ofrecé lo que sí podés hacer.
  - Si está enojado, reconocé la molestia con empatía y pedile lo que necesitás para ayudarlo.
  - No hay documentos en esta charla, no des datos de la tienda, de pedidos ni números.
    Marcá factual en false.
cierre: Ofrecé ayuda con pedidos o consultas de la tienda."""
GENERAL_INSTRUCTIONS = """nombre: consulta_general
objetivo: Responder consultas generales sobre la tienda y sus políticas con los documentos
datos_necesarios: []
herramientas: []
condiciones:
  - Respondé con lo que dicen los documentos y citalos en fuentes.
  - Si la respuesta no está en los documentos, derivá.
cierre: Preguntá si necesita algo más."""
CHAT_FALLBACK = (
    "¡Hola! Soy el asistente virtual de {empresa}. Te puedo ayudar con tus pedidos y con "
    "consultas sobre la tienda. ¿En qué te ayudo?"
)


class _ChatFallback(Exception):
    """La charla no logró una respuesta válida: se usa el mensaje fijo, sin derivar."""


# "mi mail es", "mi correo:", "te paso mi email": lo que rodea al email cuando lo manda solo.
EMAIL_LEAD = re.compile(
    r"(?i)\b(?:(?:te\s+paso|ac[aá]\s+va|este\s+es)\s+)?(?:mi\s+)?(?:e-?mail|mail|correo"
    r"(?:\s+electr[oó]nico)?)\s*(?:es|:)?"
)
# El modelo le pide al cliente un dato personal en lugar de usar la herramienta (que dispara la
# verificación por código): se reemplaza por el flujo de verificación.
ASKS_PERSONAL_DATA = re.compile(
    r"(?i)\b(?:e-?mail|mail|correo|tel[eé]fono|celular|n[uú]mero\s+de\s+contacto|dni|documento)\b"
)


# Amenaza de denuncia o de acción legal: deriva siempre a una persona.
LEGAL = "amenaza_legal"
LEGAL_THREAT = re.compile(
    r"(?i)\b(?:denunci\w+|demand(?:a|o|ar|arlos|arlas)\b|abogad\w+|carta\s+documento|juicio"
    r"|defensa\s+(?:del?|al)\s+consumidor|acci[oó]n\s+legal|acciones\s+legales)"
)
# Ya verificado, el modelo vuelve a hablar de códigos o de verificar (lo hizo Groq).
VERIFICATION_TALK = re.compile(r"(?i)c[oó]digo|verific")
VERIFIED_CORRECTION = (
    "El cliente ya está verificado (contacto verificado=sí): no le pidas verificación, códigos "
    "ni datos personales. Usá las herramientas para resolver su consulta."
)
_FIXED_VERIFICATION = (
    "Para darte datos de tu pedido necesito verificar",
    "Si ese email está",
    "Ese código",
)


def _verification_exchange(role: str, text: str) -> bool:
    """Mensajes del intercambio de verificación (el pedido del email, el email, el código): no
    aportan al modelo y, al verlos, el modelo real volvía a pedir la verificación."""
    if role == "agent":
        return text.startswith(_FIXED_VERIFICATION)
    if role == "customer":
        rest = email_code.CODE.sub("", EMAIL_LEAD.sub("", email_code.EMAIL.sub("", text)))
        return rest.strip(" ,.;:-!") == "" and text.strip() != ""
    return False


def _wrong_code_message(remaining: int) -> str:
    left = "Te queda 1 intento" if remaining == 1 else f"Te quedan {remaining} intentos"
    return f"Ese código no es correcto. {left}."


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
    prefix: str = ""  # se antepone a la respuesta (p. ej. al terminar la verificación)
    needs_identity: bool = False  # el tipo de caso usa herramientas con datos de la cuenta
    # Clasificación ya hecha al decidir si se reabre un caso resuelto (no se clasifica dos veces).
    classification: Classification | None = None
    tone: str = "rioplatense"


def _grounding_correction(tool_use_id: str | None, reason: str) -> dict[str, Any]:
    """Devuelve al modelo la respuesta rechazada para que la reescriba (una sola vez)."""
    return _correction(
        tool_use_id,
        f"Respuesta rechazada por el control de fundamento: {reason}. Usá solo datos de "
        "<documentos> citando su id en fuentes, o resultados de herramientas; si no alcanza, "
        "derivá.",
    )


def _correction(tool_use_id: str | None, text: str) -> dict[str, Any]:
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
        mailer: Mailer | None = None,
    ) -> None:
        self._conn = conn
        self._llm = llm
        self._embedder = embedder
        self._guardian = guardian
        self._model = model
        self._max_steps = max_steps
        self._mailer = mailer or ConsoleMailer()

    # -- entrada ---------------------------------------------------------------------------
    def handle_message(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> AgentResult:
        # Un solo alcance de tenant para todo el turno: cada consulta de adentro lo reusa en
        # lugar de fijar y restaurar el rol (4 viajes a la base menos por consulta).
        with tenant_scope(self._conn, tenant_id):
            with timing.turn() as timer:
                result = self._handle(tenant_id, contact_id, text)
            # Cuánto tardó cada paso (base, modelo, búsqueda, herramientas): decisión 025.
            summary = timer.summary()
            audit_log.record(self._conn, tenant_id, result.case_id, "turn_timing", summary)
            self._observe(tenant_id, result, timer, summary)
        return result

    def _observe(
        self,
        tenant_id: uuid.UUID,
        result: AgentResult,
        timer: timing.Timer,
        summary: dict[str, Any],
    ) -> None:
        """Observabilidad del turno (decisión 028): la traza del caso y las alertas de latencia
        y de costo por caso."""
        tracing.export_turn(
            tenant_id,
            result.case_id,
            timer,
            action=result.action,
            case_type=result.case_type,
            tools=[(t.name, t.status) for t in result.tool_calls],
        )
        row = self._conn.execute(
            "select coalesce(sum(cost_usd), 0) from usage where case_id = %s", (result.case_id,)
        ).fetchone()
        turn_cost = sum(
            (Decimal(str(r["attrs"].get("llm.cost_usd") or 0)) for r in timer.records), Decimal(0)
        )
        alerts.check_turn(
            tenant_id,
            result.case_id,
            summary,
            case_cost=Decimal(row[0]) if row else Decimal(0),
            turn_cost=turn_cost,
        )

    def _handle(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> AgentResult:
        company, tone = self._tenant(tenant_id)
        contact = self._contact(tenant_id, contact_id)
        case_id, classification = self._open_case(tenant_id, contact_id, company, text)
        self._save_message(tenant_id, case_id, "customer", text)
        turn = _Turn(tenant_id=tenant_id, case_id=case_id, contact=contact, text=text, tone=tone)
        turn.classification = classification
        self._audit(turn, "message_received", {"text": text, "verified": contact.verified})
        pending: email_code.Verification | None = None
        if not contact.verified:
            pending = email_code.open_verification(self._conn, tenant_id, contact_id)
            if pending is None and email_code.EMAIL.search(text):
                # Mandó su email sin que el flujo lo pidiera (por ejemplo, porque el modelo se
                # lo pidió en texto libre): es un intento de verificarse, se le manda el código.
                email_code.request_email(
                    self._conn, tenant_id, contact_id, case_id, self._pending_question(turn)
                )
                pending = email_code.open_verification(self._conn, tenant_id, contact_id)
            if pending is not None:
                handled = self._continue_verification(turn, pending, company, tone)
                if handled is not None:
                    return handled
        if (
            pending is None
            and closing.is_confirmation(text)
            and closing.closable_by_confirmation(self._conn, tenant_id, case_id)
        ):
            # Regla de cierre: el cliente confirma ("gracias", "eso era todo").
            result = self._finish(turn, "responder", closing.CLOSING_MESSAGE)
            closing.resolve(self._conn, tenant_id, case_id, "El cliente confirmó.")
            return result
        return self._respond(turn, company, tone)

    def _continue_verification(
        self, turn: _Turn, pending: email_code.Verification, company: str, tone: str
    ) -> AgentResult | None:
        """El cliente manda el email o el código. None: es otro mensaje, sigue el flujo normal."""
        email = email_code.EMAIL.search(turn.text)
        if email is not None:
            phone = self._guardian.customer_phone(turn.tenant_id, email.group(0))
            code = email_code.send_code(self._conn, turn.tenant_id, pending, phone)
            if phone is not None:
                self._mailer.send(
                    Email(
                        to=email.group(0),
                        subject=f"Tu código de verificación de {company}",
                        body=(
                            f"Tu código para verificar tu identidad en {company} es {code}. "
                            f"Vence en {email_code.CODE_TTL_MINUTES} minutos."
                        ),
                    )
                )
            self._audit(turn, "verification_code_sent", {"registered": phone is not None})
            return self._finish(turn, "responder", CODE_SENT_MESSAGE, verification=True)
        code_match = email_code.CODE.search(turn.text)
        if pending.status != "code_sent" or code_match is None:
            return None
        outcome, remaining = email_code.check_code(
            self._conn, turn.tenant_id, turn.contact.id, pending, code_match.group(0)
        )
        self._audit(turn, "verification_checked", {"result": outcome})
        if outcome == "verified":
            # Responde la consulta que había quedado pendiente, ya como cliente verificado.
            turn.contact = self._contact(turn.tenant_id, turn.contact.id)
            if not pending.question.strip():
                return self._finish(turn, "responder", VERIFIED_PREFIX + "¿En qué te ayudo?")
            turn.text = pending.question
            turn.prefix = VERIFIED_PREFIX
            return self._respond(turn, company, tone)
        if outcome == "wrong":
            return self._finish(
                turn, "responder", _wrong_code_message(remaining), verification=True
            )
        email_code.request_email(
            self._conn, turn.tenant_id, turn.contact.id, turn.case_id, pending.question
        )
        message = CODE_EXPIRED_MESSAGE if outcome == "expired" else CODE_LOCKED_MESSAGE
        return self._finish(turn, "responder", message, verification=True)

    def _pending_question(self, turn: _Turn) -> str:
        """La consulta a responder después de verificar: lo que acompaña al email en el mismo
        mensaje o, si vino solo, el mensaje anterior del cliente en el caso."""
        rest = EMAIL_LEAD.sub("", email_code.EMAIL.sub("", turn.text)).strip(" ,.;:-")
        if len(rest.split()) >= 2:
            return rest
        with tenant_scope(self._conn, turn.tenant_id):
            row = self._conn.execute(
                "select text from case_messages where case_id = %s and role = 'customer'"
                " order by created_at desc offset 1 limit 1",
                (turn.case_id,),
            ).fetchone()
        return str(row[0]) if row else ""

    def _respond(self, turn: _Turn, company: str, tone: str) -> AgentResult:
        tenant_id, case_id, contact, text = turn.tenant_id, turn.case_id, turn.contact, turn.text
        if LEGAL_THREAT.search(text):
            # Una amenaza de denuncia o de acción legal la atiende una persona, siempre: no
            # depende de lo que decida el modelo (decisión 027).
            self._audit(turn, "classified", {"case_type": LEGAL})
            return self._escalate(turn, "El cliente amenaza con una denuncia o acción legal.")
        case_types = self._active_case_types(tenant_id)
        history = self._history(tenant_id, case_id)
        previous = next((t for role, t in reversed(history) if role == "asistente"), None)
        classification = turn.classification or classify(
            self._llm,
            tenant_id=tenant_id,
            case_id=case_id,
            company=company,
            message=text,
            case_types={name: goal for name, (goal, _) in case_types.items()},
            previous=previous,
        )
        turn.classification = None  # vale solo para el mensaje con el que se abrió el caso
        self._audit_model_call(turn, "clasificador", classification.response)
        label = classification.label
        self._audit(turn, "classified", {"case_type": label})
        if label == ASK_FOR_HUMAN:
            return self._escalate(turn, "El cliente pidió hablar con una persona.")
        if label == NOT_ALLOWED:  # inyección o datos en masa: negativa fija, sin modelo ni derivar
            return self._finish(turn, "responder", REFUSAL_MESSAGE)
        if label == CHAT:  # charla: responde solo, sin documentos, herramientas ni derivación
            return self._chat(turn, company, tone, case_types, history)
        if label == GENERAL:  # consulta general: documentos, sin herramientas ni verificación
            instructions = GENERAL_INSTRUCTIONS
        elif label in case_types:
            turn.case_type = label
            self._set_case_type(tenant_id, case_id, label)
            instructions = case_types[label][1]
        else:
            return self._escalate(turn, "El tipo de caso no está activo para la empresa.")

        tool_names = list(yaml.safe_load(instructions).get("herramientas") or [])
        manifests = self._guardian.manifests(tenant_id, tool_names)
        turn.needs_identity = any(m.get("requires_verified_contact") for m in manifests)
        # La verificación se pide recién cuando el agente intenta usar datos de la cuenta (el
        # guardián lo niega, ver _loop): una pregunta general se responde con los documentos.
        with timing.step(timing.SEARCH, "busqueda"):
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
                "content": prompt.user_turn(turn.documents, contact.verified, history, text),
            }
        ]
        return self._run_loop(turn, system, messages, manifests)

    def _chat(
        self,
        turn: _Turn,
        company: str,
        tone: str,
        case_types: dict[str, tuple[str, str]],
        history: list[tuple[str, str]],
    ) -> AgentResult:
        goals = "; ".join(goal for goal, _ in case_types.values()) or "consultas de la tienda"
        instructions = CHAT_INSTRUCTIONS.format(empresa=company, capacidades=goals)
        system = prompt.system_prompt(company, tone, instructions)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": prompt.user_turn([], turn.contact.verified, history, turn.text),
            }
        ]
        return self._run_loop(
            turn, system, messages, [], chat_fallback=CHAT_FALLBACK.format(empresa=company)
        )

    # -- ciclo de herramientas -------------------------------------------------------------
    def _run_loop(
        self,
        turn: _Turn,
        system: str,
        messages: list[dict[str, Any]],
        manifests: list[dict[str, Any]],
        *,
        chat_fallback: str | None = None,
    ) -> AgentResult:
        """`chat_fallback`: charla. Solo se ofrece la herramienta responder y, si el modelo no
        logra una respuesta válida, se usa ese mensaje fijo en lugar de derivar."""
        finals = ("responder",) if chat_fallback is not None else ("responder", "derivar")
        tools = prompt.tool_definitions(manifests, finals)
        allowed = {m["name"] for m in manifests}
        try:
            return self._loop(turn, system, messages, tools, allowed, chat=chat_fallback)
        except _ChatFallback:
            assert chat_fallback is not None
            return self._finish(turn, "responder", chat_fallback)

    def _loop(
        self,
        turn: _Turn,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        allowed: set[str],
        *,
        chat: str | None,
    ) -> AgentResult:
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
                return self._give_up(turn, "El modelo no pudo responder este pedido.", chat)
            final = next(
                (c for c in response.tool_calls if c.name in prompt.FINAL_TOOL_NAMES), None
            )
            business = [c for c in response.tool_calls if c.name not in prompt.FINAL_TOOL_NAMES]

            if not response.tool_calls:  # texto suelto: se trata como respuesta sin fuentes
                final_name, final_input, final_id = (
                    "responder",
                    {"mensaje": response.text, "fuentes": [], "factual": chat is None},
                    None,
                )
            elif final is not None and not business:
                final_name, final_input, final_id = final.name, final.input, final.id
            else:
                final_name = ""
            if final_name:
                if final_name == "responder" and self._asks_personal_data(turn, final_input):
                    return self._request_verification(turn, str(final_input.get("mensaje")))
                if final_name == "responder" and self._reverifies(turn, final_input):
                    self._audit(
                        turn,
                        "grounding_rejected",
                        {
                            "reason": "Pidió verificar a un contacto verificado.",
                            "rejected_message": final_input,
                        },
                    )
                    if turn.grounding_retries >= 1:
                        return self._give_up(turn, "Volvió a pedir la verificación.", chat)
                    turn.grounding_retries += 1
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append(_correction(final_id, VERIFIED_CORRECTION))
                    continue
                if final_name == "responder":
                    verdict = self._check_grounding(turn, final_input)
                    if not verdict.ok:
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {"reason": verdict.reason, "rejected_message": final_input},
                        )
                        if turn.grounding_retries >= 1:
                            return self._give_up(
                                turn, f"Control de fundamento: {verdict.reason}.", chat
                            )
                        turn.grounding_retries += 1
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(_grounding_correction(final_id, verdict.reason))
                        continue
                if final_name == "derivar" and chat is not None:
                    raise _ChatFallback  # en la charla no se deriva
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
                    if decision.reason == UNVERIFIED_REASON:
                        # Quiso usar datos de la cuenta sin verificación: se pide el email.
                        self._audit(
                            turn,
                            "tool_call",
                            {
                                "tool": call.name,
                                "input": call.input,
                                "status": decision.status,
                                "reason": decision.reason,
                            },
                        )
                        email_code.request_email(
                            self._conn, turn.tenant_id, turn.contact.id, turn.case_id, turn.text
                        )
                        return self._finish(
                            turn, "responder", VERIFICATION_MESSAGE, verification=True
                        )
                    if decision.reason == ALREADY_PENDING_REASON:
                        # El mismo pedido ya espera una aprobación en otra conversación: no se
                        # crea otra y se avisa con un mensaje fijo.
                        self._audit(
                            turn,
                            "tool_call",
                            {
                                "tool": call.name,
                                "input": call.input,
                                "status": decision.status,
                                "reason": decision.reason,
                            },
                        )
                        return self._finish(turn, "responder", DUPLICATE_APPROVAL_MESSAGE)
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
        return self._give_up(turn, f"Se alcanzó el límite de {self._max_steps} pasos.", chat)

    def _asks_personal_data(self, turn: _Turn, data: dict[str, Any]) -> bool:
        """Sin verificar, el modelo le pide al cliente el email, el teléfono u otro dato personal
        en texto: eso no verifica a nadie."""
        message = str(data.get("mensaje") or "")
        return (
            not turn.contact.verified
            and turn.needs_identity
            and ASKS_PERSONAL_DATA.search(message) is not None
        )

    def _reverifies(self, turn: _Turn, data: dict[str, Any]) -> bool:
        """Ya verificado, el modelo habla de enviar un código o de verificar la cuenta."""
        message = str(data.get("mensaje") or "")
        return (
            turn.contact.verified
            and turn.needs_identity
            and VERIFICATION_TALK.search(message) is not None
        )

    def _request_verification(self, turn: _Turn, rejected: str) -> AgentResult:
        """Flujo de verificación por código en lugar del pedido de datos en texto libre."""
        email_code.request_email(
            self._conn, turn.tenant_id, turn.contact.id, turn.case_id, turn.text
        )
        self._audit(
            turn,
            "verification_requested",
            {"reason": "El modelo pidió datos personales en texto.", "rejected_message": rejected},
        )
        return self._finish(turn, "responder", VERIFICATION_MESSAGE, verification=True)

    def _give_up(self, turn: _Turn, reason: str, chat: str | None) -> AgentResult:
        """Sin una respuesta válida: deriva, salvo en la charla (mensaje fijo, sin derivar) o si
        el guardián ya creó una aprobación (el pedido queda en revisión, con mensaje fijo)."""
        if chat is not None:
            raise _ChatFallback
        approval = self._approval_pending(turn, reason)
        if approval is not None:
            return approval
        return self._escalate(turn, reason)

    def _approval_pending(self, turn: _Turn, reason: str) -> AgentResult | None:
        """Si en este turno el guardián creó una aprobación, el resultado es el pedido de
        aprobación, con un mensaje fijo, sin importar qué haya hecho el modelo después: la
        decisión ya está en manos de una persona y derivar dejaría la aprobación colgando."""
        pending = next((d for d in turn.decisions if d.status == "approval_required"), None)
        if pending is None:
            return None
        self._audit(turn, "approval_message_fixed", {"reason": reason})
        return self._finish(
            turn, "pedir_aprobacion", APPROVAL_PENDING_MESSAGE, approval_id=pending.approval_id
        )

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
            tool_names={t.name for t in turn.tool_calls},
        )

    def _final_action(self, turn: _Turn, name: str, data: dict[str, Any]) -> AgentResult:
        if name == "derivar":
            reason = str(data.get("motivo") or "Derivado por el agente.")
            return self._approval_pending(turn, f"Quiso derivar: {reason}") or self._escalate(
                turn, reason
            )
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
        # Se guarda y se envía tal como lo ve el cliente.
        if turn.tone == "rioplatense":
            message = to_voseo(message)  # por si el modelo se escapa al tuteo
        message = to_whatsapp(turn.prefix + message)
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
                "select id, coalesce(verified_phone_hash, phone_hash),"
                " coalesce(verified_until > now(), false) from contacts where id = %s",
                (contact_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Contacto inexistente")
        return ContactContext(id=row[0], phone_hash=row[1], verified=row[2])

    def _open_case(
        self, tenant_id: uuid.UUID, contact_id: uuid.UUID, company: str, text: str
    ) -> tuple[uuid.UUID, Classification | None]:
        """El caso del mensaje: el que el contacto tiene abierto; si no, el que resolvió hace
        menos de 7 días por el mismo tema, que se reabre (regla de reapertura); si no, uno
        nuevo. Para saber si es el mismo tema hay que clasificar el mensaje: esa clasificación
        se devuelve para no repetirla."""
        # Regla de cierre: si su caso lleva 24 h sin respuesta, queda resuelto y empieza otro.
        closing.close_inactive(self._conn, tenant_id, contact_id=contact_id)
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select id from cases where contact_id = %s and status <> 'resolved'"
                " and archived_at is null order by created_at desc limit 1",
                (contact_id,),
            ).fetchone()
        if row is not None:
            return row[0], None
        classification: Classification | None = None
        candidate = billing.reopen_candidate(self._conn, tenant_id, contact_id)
        if candidate is not None and not closing.is_confirmation(text):
            case_types = self._active_case_types(tenant_id)
            classification = classify(
                self._llm,
                tenant_id=tenant_id,
                case_id=None,  # todavía no se sabe a qué caso va el mensaje
                company=company,
                message=text,
                case_types={name: goal for name, (goal, _) in case_types.items()},
            )
            if classification.label == candidate[1]:
                billing.reopen(self._conn, tenant_id, candidate[0])
                return candidate[0], classification
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
                (tenant_id, contact_id),
            ).fetchone()
        assert row is not None
        case_id: uuid.UUID = row[0]
        return case_id, classification

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
        return [
            (labels[role], text)
            for role, text in reversed(rows[1:])
            if not _verification_exchange(role, text)
        ]
