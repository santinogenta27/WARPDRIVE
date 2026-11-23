"""Ciclo del agente por mensaje entrante (spec: "Agente en producción").

Pasos: caso abierto o nuevo → verificación por código en curso (email o código del cliente) →
clasificación (Haiku; una inyección o un pedido de datos en masa recibe una negativa fija) →
tipo activo → verificación de identidad → RAG → ciclo de herramientas con Sonnet (máximo 8
pasos) → una de cuatro acciones: responder, ejecutar, pedir_aprobacion o derivar.
"""

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import psycopg
import yaml

from app import i18n, memory
from app.agent import conduct, manipulation, presentable, prompt, supervisor
from app.agent.classifier import (
    ASK_FOR_HUMAN,
    CHAT,
    CLASSIFIER_MODEL,
    GENERAL,
    NOT_ALLOWED,
    Classification,
    classify,
)
from app.agent.grounding import GroundingVerdict, check_grounding, check_sales, is_product_tool
from app.agent.voseo import to_voseo
from app.audit import log as audit_log
from app.cases import billing, closing, csat, team_notify
from app.channels.whatsapp.format import to_whatsapp
from app.companies import rules
from app.db.connection import tenant_scope
from app.identity import email_code
from app.llm import spending
from app.llm.client import LLMClient
from app.llm.embedder import Embedder
from app.llm.pricing import cost_usd
from app.llm.types import LLMResponse
from app.mailer import Mailer, MailError, mailer_from_env
from app.obs import alerts, timing, tracing
from app.policy.guardian import (
    ALREADY_PENDING_REASON,
    CANCEL_PENDING,
    RETENTION_REASON,
    UNVERIFIED_REASON,
    ContactContext,
    GuardDecision,
    Guardian,
)
from app.rag.search import SearchResult, hybrid_search
from app.sales import upsell

RETENTION_OFFER_ONLY = {
    "es": (
        "Esta alternativa se acaba de ofrecer: ofrecela con palabras y hacela solo si el cliente "
        "la acepta en su próximo mensaje."
    ),
    "en": (
        "This alternative was just offered: offer it in words and do it only if the customer "
        "accepts it in their next message."
    ),
}
RETENTION_WAIT = {
    "es": "Las alternativas se acaban de ofrecer: respondé y esperá lo que diga el cliente.",
    "en": "The alternatives were just offered: answer and wait for what the customer says.",
}

AGENT_MODEL = "claude-sonnet-5-5"
MAX_STEPS = 8
Action = Literal["responder", "ejecutar", "pedir_aprobacion", "derivar"]

# Los mensajes fijos al cliente están en app/i18n.py, en español y en inglés (decisión 036).
# Estos nombres son los de español, que ya usaban los tests.
_ES = i18n.MESSAGES["es"]
VERIFICATION_MESSAGE = _ES["verification"]
# Con la aprobación creada, si el modelo no da una respuesta válida (o quiere derivar).
APPROVAL_PENDING_MESSAGE = _ES["approval_pending"]
# El mismo pedido ya tiene una solicitud pendiente desde otra conversación.
DUPLICATE_APPROVAL_MESSAGE = _ES["duplicate_approval"]
ESCALATION_MESSAGE = _ES["escalation"]
REFUSAL_MESSAGE = _ES["refusal"]
# Verificación por código: misma respuesta exista o no el email, para no revelarlo.
CODE_SENT_MESSAGE = _ES["code_sent"]
VERIFIED_PREFIX = _ES["verified_prefix"]
CODE_FAILED_MESSAGE = _ES["code_failed"]
CODE_EXPIRED_MESSAGE = _ES["code_expired"]
CODE_LOCKED_MESSAGE = _ES["code_locked"]


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
CHAT_FALLBACK = _ES["chat_fallback"]
# Las mismas instrucciones para una empresa en inglés. Las claves del YAML no se traducen.
CHAT_INSTRUCTIONS_EN = """nombre: conversacion_general
objetivo: Chat with the customer in a friendly way and guide them to what the assistant can solve
condiciones:
  - Answer in one or two sentences, warm and brief, without handing off.
  - If they greet you, thank you or say goodbye, reply in kind and offer help.
  - If they ask whether you're a bot or who they're talking to, say you're the virtual
    assistant of {empresa}.
  - If they ask what you can do, tell them how you can help ({capacidades}).
  - If they ask about something unrelated to the store, kindly say you can't help with that
    and offer what you can do.
  - If they're upset, acknowledge it with empathy and ask for what you need to help them.
  - There are no documents in this chat: don't give store data, order details or numbers.
    Set factual to false.
cierre: Offer help with orders or questions about the store."""
GENERAL_INSTRUCTIONS_EN = """nombre: consulta_general
objetivo: Answer general questions about the store and its policies using the documents
datos_necesarios: []
herramientas: []
condiciones:
  - Answer with what the documents say and cite them in fuentes.
  - If the answer is not in the documents, hand off.
cierre: Ask if they need anything else."""


class _ChatFallback(Exception):
    """La charla no logró una respuesta válida: se usa el mensaje fijo, sin derivar."""


# "mi mail es", "mi correo:", "te paso mi email": lo que rodea al email cuando lo manda solo.
EMAIL_LEAD = re.compile(
    r"(?i)\b(?:(?:te\s+paso|ac[aá]\s+va|este\s+es)\s+)?(?:mi\s+)?(?:e-?mail|mail|correo"
    r"(?:\s+electr[oó]nico)?)\s*(?:es|:)?"
    # En inglés: "my email is", "here's my email:", "email address:".
    r"|(?i:\b(?:here(?:'s|\s+is)\s+)?(?:my\s+)?e-?mail(?:\s+address)?\s*(?:is|:)?)"
)
# Pide datos de otra persona por su email (decisión 069): "los pedidos de x@", "the orders of
# x@", "el cliente x@", "x@'s address". A ese email no se le manda ningún código.
_DATA_WORDS = (
    r"pedidos?|compras?|direcci\w*|datos|tel[eé]fono|celular|cuenta|historial|env[ií]os?"
    r"|orders?|address(?:es)?|phone|account|details|info\w*|purchases?|shipments?"
)
THIRD_PARTY_EMAIL = re.compile(
    rf"(?i)(?<!\bmis\s)(?<!\bmi\s)(?<!\bmy\s)\b(?:{_DATA_WORDS})\b[^@\n]{{0,40}}?"
    r"\b(?:de|del|of|for|from)\s+(?:(?:el|la|los|the)\s+)?(?:\S+\s+)?\S+@"
    r"|\b(?:cliente|clienta|customer|usuari[oa]|user|persona|person)\s+\S+@"
    r"|\S+@\S+\s+(?:ordered|bought|purchased|compr[oó]|pidi[oó])\b"
    r"|\S+@\S+?'s\b"
)
OWN_EMAIL = re.compile(r"(?i)\b(?:mi|my)\s+(?:e-?mail|mail|correo)\b")
# El modelo le pide al cliente un dato personal en lugar de usar la herramienta (que dispara la
# verificación por código): se reemplaza por el flujo de verificación.
ASKS_PERSONAL_DATA = re.compile(
    r"(?i)\b(?:e-?mail|mail|correo|tel[eé]fono|celular|n[uú]mero\s+de\s+contacto|dni|documento)\b"
)
ASKS_PERSONAL_DATA_EN = re.compile(
    r"(?i)\b(?:e-?mail|phone(?:\s+number)?|mobile(?:\s+number)?|cell(?:\s+number)?"
    r"|contact\s+number|date\s+of\s+birth|ssn|social\s+security|(?:id|identity)\s+(?:number|card)"
    r"|driver'?s\s+license|home\s+address)\b"
)


# Amenaza de denuncia o de acción legal: deriva siempre a una persona.
LEGAL = "amenaza_legal"
LEGAL_THREAT = re.compile(
    r"(?i)\b(?:denunci\w+|demand(?:a|o|ar|arlos|arlas)\b|abogad\w+|carta\s+documento|juicio"
    r"|defensa\s+(?:del?|al)\s+consumidor|acci[oó]n\s+legal|acciones\s+legales)"
)
# En inglés. Se controlan los dos idiomas en cualquier empresa: una amenaza legal la atiende una
# persona aunque llegue en el otro idioma.
LEGAL_THREAT_EN = re.compile(
    r"(?i)\b(?:sue|suing|sued|lawsuit|lawyer|attorney|legal\s+action|take\s+(?:you|this|it)\s+"
    r"to\s+court|small\s+claims|class\s+action|consumer\s+protection|attorney\s+general"
    r"|better\s+business\s+bureau|bbb|file\s+a\s+(?:formal\s+)?complaint\s+with)\b"
)
# Ya verificado, el modelo vuelve a pedir la verificación de identidad o un código (lo hizo
# Groq). Verificar otra cosa ("revisá el email de confirmación para verificarlo", "el código de
# seguimiento") no cuenta: lo hizo gpt-oss-20b con un pedido ajeno y el caso terminaba derivado.
VERIFICATION_TALK = re.compile(
    r"(?i)verific\w*\s+(?:tu|su)\s+(?:identidad|cuenta|titularidad)"
    r"|verificarte\b|verificar\s+que\s+sos"
    r"|verificaci[oó]n\s+(?:de\s+(?:tu\s+)?(?:identidad|cuenta)|por\s+c[oó]digo)"
    r"|c[oó]digo\s+(?:de\s+(?:verificaci[oó]n|seguridad|acceso|\d+\s+d[ií]gitos)|que\s+te)"
    r"|\b(?:env[ií]\w*|mand\w*|ingres\w*|pas[aáeo]\w*)\s+(?:\w+\s+){0,2}c[oó]digo"
    r"(?!\s+(?:de\s+)?(?:seguimiento|postal))"
)
# Lo mismo en inglés: "verify your identity", "I sent you a code", "enter the verification
# code". Un "tracking code", un "zip code" o "verify the order number" no cuentan.
VERIFICATION_TALK_EN = re.compile(
    r"(?i)verify\s+(?:your|the\s+account\s+holder'?s?)\s+(?:identity|account|ownership)"
    r"|verify\s+(?:that\s+)?you(?:'re|\s+are)\b|verify\s+you\b"
    r"|(?:identity|account)\s+verification|verification\s+code|security\s+code|one-time\s+code"
    r"|\d[\s-]*digit\s+code"
    r"|\b(?:sen[dt]|email(?:ed)?|text(?:ed)?|enter|type|share|provide|forward)\w*\s+"
    r"(?:\w+\s+){0,3}(?:a\s+|the\s+)?code\b(?<!tracking\scode)(?<!zip\scode)(?<!promo\scode)"
    r"(?<!discount\scode)(?<!coupon\scode)(?<!postal\scode)"
)
VERIFICATION_TALKS = {"es": VERIFICATION_TALK, "en": VERIFICATION_TALK_EN}
PERSONAL_DATA = {"es": ASKS_PERSONAL_DATA, "en": ASKS_PERSONAL_DATA_EN}
FORMAT_CORRECTION = (
    "Respuesta rechazada: llegó vacía o con JSON, metadatos o nombres de herramientas. Terminá "
    "usando la herramienta responder (o derivar), con el texto para el cliente solo en mensaje."
)
VERIFIED_CORRECTION = (
    "El cliente ya está verificado (contacto verificado=sí): no le pidas verificación, códigos "
    "ni datos personales. Usá las herramientas para resolver su consulta."
)
CORRECTIONS = {
    "es": {"format": FORMAT_CORRECTION, "verified": VERIFIED_CORRECTION},
    "en": {
        "format": (
            "Answer rejected: it was empty or had JSON, metadata or tool names. Finish with the "
            "responder tool (or derivar), with only the text for the customer in mensaje."
        ),
        "verified": (
            'The customer is already verified (contacto verificado="sí"): don\'t ask them for '
            "verification, codes or personal data. Use the tools to resolve their request."
        ),
    },
}
# Derivar con los datos ya en la mano (decisión 045): en la demo, gpt-oss-20b consultó el pedido
# y, en lugar de dar su estado, derivó. Se le pide una sola vez que responda con esos datos; si
# vuelve a derivar, se deriva.
HANDOFF_REJECTED_REASON = "Quiso derivar con los datos de una herramienta ya disponibles."
HANDOFF_CORRECTIONS = {
    "es": (
        "Derivación rechazada: una herramienta ya devolvió los datos de este pedido. Respondé al "
        "cliente con responder, usando esos datos y los documentos. Derivá solo si el cliente "
        "pidió una persona o si es un problema que tus herramientas no resuelven; en ese caso, "
        "volvé a usar derivar."
    ),
    "en": (
        "Hand-off rejected: a tool already returned the data for this request. Answer the "
        "customer with responder, using that data and the documents. Hand off only if the "
        "customer asked for a person or it's a problem your tools can't solve; if so, use "
        "derivar again."
    ),
}
# Por qué se derivó un caso (decisión 045): va en la auditoría como `causa` ({"tipo": ...,
# "detalle": ...}), sin datos del cliente, y "Behind the scenes" lo muestra en una línea.
HANDOFF_CAUSES = (
    "pidio_persona",
    "amenaza_legal",
    "tipo_inactivo",
    "no_resuelto",
    "rechazo_modelo",
    "formato",  # detalle: el problema de presentable.ISSUES
    "reverificar",
    "accion_sin_herramienta",
    "cobro_afirmado",  # dijo que cobró o compró (decisión 060)
    "tope_de_gasto",
    "fundamento",  # detalle: el control de grounding.KINDS
    "limite_pasos",
    "agente",  # el agente eligió derivar; `reconsiderada`: se le pidió responder antes
    "supervisor",  # detalle: `derivar` o `reescribir` (lo pidió dos veces), decisión 079
)
_FIXED_VERIFICATION = (
    "Para darte datos de tu pedido necesito verificar",
    "Si ese email está",
    "Ese código",
    "To share details about your order, I first need",
    "If that email is linked",
    "That code",
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


def _check[T](control: Callable[..., T], *args: Any) -> T:
    """Un control de la respuesta en código, medido como `verificaciones` (decisión 044)."""
    with timing.step(timing.CHECKS, timing.CHECKS_NAME):
        return control(*args)


def _lacks_empathy(text: str, said: str, language: str) -> bool:
    """Cliente molesto y respuesta sin empatía (decisión 040)."""
    return conduct.is_upset(text, language) and not conduct.shows_empathy(said, language)


def _wrong_code_message(remaining: int, language: str = "es") -> str:
    if remaining == 1:
        return i18n.text(language, "code_wrong_one")
    return i18n.text(language, "code_wrong_many", n=remaining)


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
    format_retries: int = 0  # respuestas vacías o con JSON o metadatos (presentable.py)
    tone_retries: int = 0  # cliente molesto y respuesta sin empatía (conduct.py)
    handoff_retries: int = 0  # derivó con los datos de una herramienta (decisión 045)
    prefix: str = ""  # se antepone a la respuesta (p. ej. al terminar la verificación)
    needs_identity: bool = False  # el tipo de caso usa herramientas con datos de la cuenta
    sales: bool = False  # el turno ofrece la búsqueda de productos (decisión 059)
    retained: set[str] = field(default_factory=set)  # retención ofrecida en el turno (062)
    asked_given_order: bool = False  # ya se corrigió que pidiera el pedido dado (066)
    offers: list[str] = field(default_factory=list)  # venta cruzada disponible (decisión 061)
    alternatives: set[str] = field(default_factory=set)  # de la retención de este turno (071)
    sales_nudged: bool = False  # ya se corrigió una respuesta de ventas a medias (071)
    # Clasificación ya hecha al decidir si se reabre un caso resuelto (no se clasifica dos veces).
    classification: Classification | None = None
    tone: str = "rioplatense"
    lang: str = "es"  # idioma de la empresa (decisión 036)
    memory: str = ""  # memoria del cliente verificado (decisión 078), para el prompt
    rules: str = ""  # instrucciones del tipo de caso, para el supervisor (decisión 079)
    supervisor_retries: int = 0  # el supervisor ya pidió una reescritura (decisión 079)

    def say(self, key: str, **values: Any) -> str:
        return i18n.text(self.lang, key, **values)


def _grounding_correction(
    tool_use_id: str | None, reason: str, language: str = "es"
) -> dict[str, Any]:
    """Devuelve al modelo la respuesta rechazada para que la reescriba (una sola vez)."""
    if language == "en":
        text = (
            f"Answer rejected by the grounding check: {reason}. Use only data from "
            "<documentos> citing its id in fuentes, or tool results; if that's not enough, "
            "hand off."
        )
    else:
        text = (
            f"Respuesta rechazada por el control de fundamento: {reason}. Usá solo datos de "
            "<documentos> citando su id en fuentes, o resultados de herramientas; si no alcanza, "
            "derivá."
        )
    return _correction(tool_use_id, text)


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
        self._mailer = mailer or mailer_from_env()
        self._assistant = ""  # nombre del asistente de la empresa del turno (decisión 052)
        self._supervisor_on = True  # supervisor de respuestas de la empresa (decisión 079)

    # -- entrada ---------------------------------------------------------------------------
    def handle_message(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> AgentResult:
        # Un solo alcance de tenant para todo el turno: cada consulta de adentro lo reusa en
        # lugar de fijar y restaurar el rol (4 viajes a la base menos por consulta).
        with tenant_scope(self._conn, tenant_id):
            with timing.turn() as timer:
                result = self._handle(tenant_id, contact_id, text)
            # Cuánto tardó cada paso (base, modelo, búsqueda, herramientas): decisión 025. Con
            # cada llamada y su papel, también en el log del servidor (decisión 044).
            summary = timer.summary(roles={CLASSIFIER_MODEL: "clasificador", self._model: "agente"})
            timing.log_turn(result.case_id, summary)
            audit_log.record(self._conn, tenant_id, result.case_id, "turn_timing", summary)
            self._observe(tenant_id, result, timer, summary)
            self._notify_team(tenant_id, result)
        return result

    def _notify_team(self, tenant_id: uuid.UUID, result: AgentResult) -> None:
        """Un caso derivado o una aprobación pendiente: aviso por email al equipo (decisión
        054), una sola vez por caso y por aprobación."""
        if result.action == "derivar":
            reason = str((result.escalation_summary or {}).get("motivo") or "")
            team_notify.notify(self._conn, tenant_id, result.case_id, "escalated", reason=reason)
        elif result.action == "pedir_aprobacion":
            pending = [t.name for t in result.tool_calls if t.status == "approval_required"]
            team_notify.notify(
                self._conn,
                tenant_id,
                result.case_id,
                "approval",
                reason=", ".join(pending) or "una acción",
                approval_id=result.approval_id,
            )

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
        # Aviso al cruzar el 80 % y el 100 % del tope de gasto del mes (decisión 049).
        calls = sum(1 for r in timer.records if "llm.cost_usd" in r["attrs"])
        spending.check_turn(self._conn, tenant_id, turn_cost=turn_cost, turn_calls=calls)

    def _handle(self, tenant_id: uuid.UUID, contact_id: uuid.UUID, text: str) -> AgentResult:
        company, tone, lang = self._tenant(tenant_id)
        contact = self._contact(tenant_id, contact_id)
        rated = self._csat_answer(tenant_id, contact, text, tone, lang)
        if rated is not None:
            return rated
        # Tope de gasto mensual alcanzado (decisión 049): deriva sin llamar al modelo.
        capped = spending.over(self._conn, tenant_id)
        case_id, classification = self._open_case(
            tenant_id, contact_id, company, text, lang, classify_reopen=not capped
        )
        self._save_message(tenant_id, case_id, "customer", text)
        turn = _Turn(
            tenant_id=tenant_id, case_id=case_id, contact=contact, text=text, tone=tone, lang=lang
        )
        turn.classification = classification
        self._audit(turn, "message_received", {"text": text, "verified": contact.verified})
        if capped:
            return self._escalate(
                turn, "Tope de gasto mensual alcanzado.", {"tipo": "tope_de_gasto"}
            )
        if not contact.verified and self._asks_about_someone_else(turn, company):
            # Privacidad (decisión 069): el email de otra persona nunca recibe un código.
            self._audit(turn, "classified", {"case_type": NOT_ALLOWED})
            return self._finish(turn, "responder", turn.say("refusal"))
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
            # Regla de cierre: el cliente confirma ("gracias", "eso era todo"). Con el cierre
            # va la pregunta de si se resolvió (CSAT, decisión 033), una sola vez por caso.
            ask = csat.ask(self._conn, tenant_id, case_id)
            message = turn.say("closing") + (f"\n\n{turn.say('csat_question')}" if ask else "")
            result = self._finish(turn, "responder", message)
            closing.resolve(self._conn, tenant_id, case_id, "El cliente confirmó.")
            return result
        return self._respond(turn, company, tone)

    def _asks_about_someone_else(self, turn: _Turn, company: str) -> bool:
        """El mensaje trae un email y pide datos de otra persona (decisión 069). Primero un
        control en código; si el email viene con una consulta, también el clasificador. Con un
        email y sin negativa, el turno sigue por la verificación, que no vuelve a clasificar."""
        if email_code.EMAIL.search(turn.text) is None:
            return False
        if THIRD_PARTY_EMAIL.search(turn.text) and not OWN_EMAIL.search(turn.text):
            return True
        rest = EMAIL_LEAD.sub("", email_code.EMAIL.sub("", turn.text)).strip(" ,.;:-?!¿¡")
        if len(rest.split()) < 2:
            return False  # solo el email: es el suyo para verificarse
        classification = turn.classification
        turn.classification = None
        if classification is None:
            case_types = self._active_case_types(turn.tenant_id)
            history = self._history(turn.tenant_id, turn.case_id)
            classification = classify(
                self._llm,
                tenant_id=turn.tenant_id,
                case_id=turn.case_id,
                company=company,
                message=turn.text,
                case_types={name: goal for name, (goal, _) in case_types.items()},
                previous=next((t for role, t in reversed(history) if role == "asistente"), None),
                language=turn.lang,
            )
        self._audit_model_call(turn, "clasificador", classification.response)
        return classification.label == NOT_ALLOWED

    def _csat_answer(
        self, tenant_id: uuid.UUID, contact: ContactContext, text: str, tone: str, lang: str
    ) -> AgentResult | None:
        """El cliente responde si su consulta se resolvió (decisión 033). None: no hay una
        pregunta pendiente o el mensaje es otra cosa, y sigue el flujo normal."""
        case_id = csat.pending(self._conn, tenant_id, contact.id)
        parsed = csat.parse(text) if case_id is not None else None
        if case_id is None or parsed is None:
            return None
        resolved, comment = parsed
        self._save_message(tenant_id, case_id, "customer", text)
        turn = _Turn(
            tenant_id=tenant_id, case_id=case_id, contact=contact, text=text, tone=tone, lang=lang
        )
        csat.answer(self._conn, tenant_id, case_id, resolved, comment)
        self._audit(turn, "csat_answered", {"resuelto": resolved, "con_comentario": bool(comment)})
        if resolved:
            return self._finish(turn, "responder", turn.say("csat_thanks"))
        # Dijo que no: el caso se reabre (ya no se cobra) y lo atiende una persona.
        billing.reopen(self._conn, tenant_id, case_id)
        turn.prefix = turn.say("csat_sorry")
        return self._escalate(turn, csat.NOT_RESOLVED, {"tipo": "no_resuelto"})

    def _continue_verification(
        self, turn: _Turn, pending: email_code.Verification, company: str, tone: str
    ) -> AgentResult | None:
        """El cliente manda el email o el código. None: es otro mensaje, sigue el flujo normal."""
        email = email_code.EMAIL.search(turn.text)
        if email is not None:
            phone = self._guardian.customer_phone(turn.tenant_id, email.group(0))
            code = email_code.send_code(self._conn, turn.tenant_id, pending, phone, email.group(0))
            if phone is not None:
                letter = email_code.code_email(
                    to=email.group(0), company=company, code=code, language=turn.lang
                )
                try:
                    with timing.step(timing.MAIL, "codigo de verificacion"):
                        self._mailer.send(letter)
                except MailError as error:
                    return self._code_not_sent(turn, pending, str(error))
            self._audit(turn, "verification_code_sent", {"registered": phone is not None})
            return self._finish(turn, "responder", turn.say("code_sent"), verification=True)
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
                return self._finish(
                    turn, "responder", turn.say("verified_prefix") + turn.say("how_can_i_help")
                )
            turn.text = pending.question
            turn.prefix = turn.say("verified_prefix")
            return self._respond(turn, company, tone)
        if outcome == "wrong":
            return self._finish(
                turn, "responder", _wrong_code_message(remaining, turn.lang), verification=True
            )
        email_code.request_email(
            self._conn, turn.tenant_id, turn.contact.id, turn.case_id, pending.question
        )
        message = turn.say("code_expired" if outcome == "expired" else "code_locked")
        return self._finish(turn, "responder", message, verification=True)

    def _code_not_sent(
        self, turn: _Turn, pending: email_code.Verification, detail: str
    ) -> AgentResult:
        """El proveedor de email falló (decisión 029): el cliente lo sabe y puede pedir otro
        código. El que no llegó deja de valer, y la consulta pendiente se conserva."""
        email_code.request_email(
            self._conn, turn.tenant_id, turn.contact.id, turn.case_id, pending.question
        )
        self._audit(turn, "verification_code_failed", {"motivo": detail})
        with tenant_scope(self._conn, turn.tenant_id):
            self._conn.execute(
                "insert into error_events (tenant_id, kind, detail) values (%s, %s, %s)",
                (turn.tenant_id, "MailError", detail[:500]),
            )
        alerts.record_error(turn.tenant_id, "MailError")
        return self._finish(turn, "responder", turn.say("code_failed"), verification=True)

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
        if LEGAL_THREAT.search(text) or LEGAL_THREAT_EN.search(text):
            # Una amenaza de denuncia o de acción legal la atiende una persona, siempre: no
            # depende de lo que decida el modelo (decisión 027).
            self._audit(turn, "classified", {"case_type": LEGAL})
            return self._escalate(
                turn,
                "El cliente amenaza con una denuncia o acción legal.",
                {"tipo": "amenaza_legal"},
            )
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
            language=turn.lang,
            current=self._current_case_type(tenant_id, case_id) if history else None,
        )
        turn.classification = None  # vale solo para el mensaje con el que se abrió el caso
        self._audit_model_call(turn, "clasificador", classification.response)
        label = classification.label
        self._audit(turn, "classified", {"case_type": label})
        if label == ASK_FOR_HUMAN:
            return self._escalate(
                turn, "El cliente pidió hablar con una persona.", {"tipo": "pidio_persona"}
            )
        claimed_role = label == NOT_ALLOWED and manipulation.on_own_order(text)
        if claimed_role:
            # Falso gerente (decisión 070): un cargo o "saltate la política" sobre un pedido
            # puntual se atiende como cualquier consulta de ese pedido. Se clasifica el tema sin
            # el cargo; solo vale un tipo de caso activo, y los controles son los de siempre.
            topic = classify(
                self._llm,
                tenant_id=tenant_id,
                case_id=case_id,
                company=company,
                message=manipulation.topic(text),
                case_types={name: goal for name, (goal, _) in case_types.items()},
                previous=previous,
                language=turn.lang,
                # Con las demás etiquetas, el modelo real tomaba "un cupón del 50 %" como
                # consulta general (sin herramientas) y quedaba la negativa fija.
                only_case_types=True,
            )
            self._audit_model_call(turn, "clasificador", topic.response)
            if topic.label in case_types:
                classification, label = topic, topic.label
                self._audit(turn, "classified", {"case_type": label, "cargo_declarado": True})
        if label == NOT_ALLOWED:  # inyección o datos en masa: negativa fija, sin modelo ni derivar
            return self._finish(turn, "responder", turn.say("refusal"))
        if label == CHAT:  # charla: responde solo, sin documentos, herramientas ni derivación
            return self._chat(turn, company, tone, case_types, history)
        if label == GENERAL:  # consulta general: documentos, sin herramientas ni verificación
            instructions = GENERAL_INSTRUCTIONS_EN if turn.lang == "en" else GENERAL_INSTRUCTIONS
        elif label in case_types:
            turn.case_type = label
            self._set_case_type(tenant_id, case_id, label)
            instructions = case_types[label][1]
        else:
            return self._escalate(
                turn, "El tipo de caso no está activo para la empresa.", {"tipo": "tipo_inactivo"}
            )

        tool_names = list(yaml.safe_load(instructions).get("herramientas") or [])
        if label in case_types and classification.also in case_types:
            # Dos temas en el mismo mensaje (decisión 040): las instrucciones y las herramientas
            # de los dos tipos de caso. El caso queda con el tipo principal.
            second = case_types[str(classification.also)][1]
            self._audit(turn, "second_topic", {"case_type": classification.also})
            tool_names += [
                name
                for name in yaml.safe_load(second).get("herramientas") or []
                if name not in tool_names
            ]
            instructions = f"{instructions.strip()}\n---\n{second.strip()}"
        retained = self._retention_note(turn, tool_names)
        if retained:
            instructions = f"{instructions.strip()}\n{retained}"
        if claimed_role:
            note = manipulation.NOTE["en" if turn.lang == "en" else "es"]
            instructions = f"{instructions.strip()}\n{note}"
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
        self._load_memory(turn)
        turn.rules = instructions
        system = prompt.system_prompt(
            company, tone, instructions, self._assistant, memory=bool(turn.memory)
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": prompt.user_turn(
                    turn.documents, contact.verified, history, text, memory=turn.memory
                ),
            }
        ]
        return self._run_loop(turn, system, messages, manifests)

    def _load_memory(self, turn: _Turn) -> None:
        """La memoria del cliente (decisión 078), solo con el contacto verificado: la del
        cliente que demostró su identidad, nunca otra."""
        turn.memory = ""
        if not turn.contact.verified:
            return
        found = memory.load(
            self._conn, turn.tenant_id, turn.contact.phone_hash, exclude_case=turn.case_id
        )
        if found is None:
            return
        turn.memory = memory.render(found, turn.lang)
        if turn.memory:
            self._audit(
                turn,
                "memory_used",
                {
                    "compras": len(found.purchases),
                    "casos_abiertos": len(found.open_cases),
                    "preferencias": len(found.preferences),
                },
            )

    def _remember(self, turn: _Turn, action: Action) -> None:
        """Suma el turno a la memoria del cliente verificado (decisión 078). Compras y nombre,
        solo de pedidos que pasaron el control de dueño del guardián."""
        if not turn.contact.verified:
            return
        orders = [
            resource
            for decision in turn.decisions
            for resource in decision.resources.values()
            if isinstance(resource, dict)
        ]
        memory.remember(
            self._conn,
            turn.tenant_id,
            turn.contact.phone_hash,
            case_id=turn.case_id,
            case_type=turn.case_type,
            text=turn.text,
            orders=orders,
            order_ids=[str(o["id"]) for o in orders if o.get("id")],
            outcome=memory.OUTCOMES[action],
        )

    def _chat(
        self,
        turn: _Turn,
        company: str,
        tone: str,
        case_types: dict[str, tuple[str, str]],
        history: list[tuple[str, str]],
    ) -> AgentResult:
        goals = "; ".join(goal for goal, _ in case_types.values()) or turn.say("store_questions")
        template = CHAT_INSTRUCTIONS_EN if turn.lang == "en" else CHAT_INSTRUCTIONS
        instructions = template.format(empresa=company, capacidades=goals)
        self._load_memory(turn)
        system = prompt.system_prompt(
            company, tone, instructions, self._assistant, memory=bool(turn.memory)
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": prompt.user_turn(
                    [], turn.contact.verified, history, turn.text, memory=turn.memory
                ),
            }
        ]
        return self._run_loop(
            turn, system, messages, [], chat_fallback=turn.say("chat_fallback", empresa=company)
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
        # Con algo pendiente de aprobación, el agente puede cancelarlo si el cliente cambia de
        # idea (decisión 040).
        pending = chat_fallback is None and self._has_pending(turn.tenant_id, turn.case_id)
        # Venta cruzada (decisión 061): solo si hay una regla para el tipo de caso, no hubo otra
        # oferta en la conversación y es buen momento. El guardián lo vuelve a controlar.
        offers = (
            upsell.offerable(self._conn, turn.tenant_id, turn.case_id, turn.case_type, turn.text)
            if chat_fallback is None
            else []
        )
        tools = prompt.tool_definitions(
            manifests,
            finals,
            english=turn.lang == "en",
            pending=pending,
            offers=[(r.product, r.max_discount) for r in offers],
        )
        allowed = {m["name"] for m in manifests} | ({CANCEL_PENDING} if pending else set())
        allowed |= {upsell.OFFER_PRODUCT} if offers else set()
        turn.sales = any(is_product_tool(m) for m in manifests)
        turn.offers = [r.product for r in offers]
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
                return self._give_up(
                    turn,
                    "El modelo no pudo responder este pedido.",
                    chat,
                    {"tipo": "rechazo_modelo"},
                )
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
                # El JSON de la herramienta final escrito como texto (lo hizo gpt-oss-120b): si
                # trae una respuesta válida, se usa esa en lugar de mandar el JSON.
                loose = presentable.parse_loose(response.text)
                if loose is not None and loose.get("accion") == "derivar":
                    final_name, final_input = "derivar", {"motivo": loose["motivo"]}
                elif loose is not None:
                    final_input = {**loose, "factual": loose["factual"] and chat is None}
            elif final is not None and not business:
                final_name, final_input, final_id = final.name, final.input, final.id
            else:
                final_name = ""
            if final_name == "responder":
                # Vacío, JSON o metadatos nunca llegan al cliente: un reintento y después deriva.
                issue = _check(presentable.problem, str(final_input.get("mensaje") or ""))
                if issue is not None:
                    self._audit(
                        turn, "format_rejected", {"reason": issue, "rejected_message": final_input}
                    )
                    if turn.format_retries >= 1:
                        return self._give_up(
                            turn,
                            f"Respuesta impresentable: {issue}.",
                            chat,
                            {"tipo": "formato", "detalle": issue},
                        )
                    turn.format_retries += 1
                    if response.tool_calls or response.text.strip():  # sin bloques vacíos
                        messages.append({"role": "assistant", "content": response.content})
                    messages.append(_correction(final_id, CORRECTIONS[turn.lang]["format"]))
                    continue
            if final_name:
                if final_name == "responder" and _check(
                    self._asks_personal_data, turn, final_input
                ):
                    return self._request_verification(turn, str(final_input.get("mensaje")))
                if final_name == "responder" and _check(self._reverifies, turn, final_input):
                    self._audit(
                        turn,
                        "grounding_rejected",
                        {
                            "reason": "Pidió verificar a un contacto verificado.",
                            "rejected_message": final_input,
                        },
                    )
                    if turn.grounding_retries >= 1:
                        return self._give_up(
                            turn, "Volvió a pedir la verificación.", chat, {"tipo": "reverificar"}
                        )
                    turn.grounding_retries += 1
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append(_correction(final_id, CORRECTIONS[turn.lang]["verified"]))
                    continue
                if final_name == "responder":
                    # Controles de conducta (decisión 040): una acción afirmada sin herramienta
                    # es una respuesta inventada; con el cliente molesto, primero la empatía.
                    said = str(final_input.get("mensaje") or "")
                    if _check(conduct.claims_payment, said):  # nunca cobra (decisión 060)
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {"reason": conduct.PAYMENT_REASON, "rejected_message": final_input},
                        )
                        if turn.grounding_retries >= 1:
                            return self._give_up(
                                turn,
                                f"Control de fundamento: {conduct.PAYMENT_REASON}.",
                                chat,
                                {"tipo": "cobro_afirmado"},
                            )
                        turn.grounding_retries += 1
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(
                            _correction(final_id, conduct.PAYMENT_CORRECTIONS[turn.lang])
                        )
                        continue
                    # "Reembolsado" con la aprobación pendiente tampoco vale (decisión 048).
                    if (
                        not self._acted(turn) and _check(conduct.claims_action, said, turn.lang)
                    ) or (
                        not self._executed(turn)
                        and _check(conduct.claims_completion, said, turn.lang)
                    ):
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {
                                "reason": conduct.ACTION_CLAIM_REASON,
                                "rejected_message": final_input,
                            },
                        )
                        if turn.grounding_retries >= 1:
                            return self._give_up(
                                turn,
                                f"Control de fundamento: {conduct.ACTION_CLAIM_REASON}.",
                                chat,
                                {"tipo": "accion_sin_herramienta"},
                            )
                        turn.grounding_retries += 1
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(
                            _correction(final_id, conduct.ACTION_CORRECTIONS[turn.lang])
                        )
                        continue
                    given = _check(conduct.asks_given_order, said, turn.text)
                    if given and not turn.asked_given_order:
                        # Pide el número de pedido que el cliente ya dio (decisión 066): se le
                        # pide usarlo, una sola vez.
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {
                                "reason": "Pidió un dato que el cliente ya dio.",
                                "rejected_message": final_input,
                            },
                        )
                        turn.asked_given_order = True
                        messages.append({"role": "assistant", "content": response.content})
                        correction = conduct.GIVEN_ORDER_CORRECTIONS[turn.lang].format(order=given)
                        messages.append(_correction(final_id, correction))
                        continue
                    pending_sale = (
                        _check(self._sales_correction, turn, said)
                        if turn.sales and not turn.sales_nudged
                        else None
                    )
                    if pending_sale:
                        # Ventas a medias (decisión 071): sin precio, un carrito prometido o la
                        # venta cruzada salteada. Se corrige una sola vez por turno.
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {"reason": "Venta a medias.", "rejected_message": final_input},
                        )
                        turn.sales_nudged = True
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(_correction(final_id, pending_sale))
                        continue
                    if turn.tone_retries == 0 and _check(
                        _lacks_empathy, turn.text, said, turn.lang
                    ):
                        self._audit(
                            turn,
                            "tone_rejected",
                            {
                                "reason": "Cliente molesto, sin empatía.",
                                "rejected_message": final_input,
                            },
                        )
                        turn.tone_retries += 1
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(_correction(final_id, conduct.TONE_CORRECTIONS[turn.lang]))
                        continue
                    verdict = _check(self._check_grounding, turn, final_input)
                    if not verdict.ok:
                        self._audit(
                            turn,
                            "grounding_rejected",
                            {"reason": verdict.reason, "rejected_message": final_input},
                        )
                        if turn.grounding_retries >= 1:
                            return self._give_up(
                                turn,
                                f"Control de fundamento: {verdict.reason}.",
                                chat,
                                {"tipo": "fundamento", "detalle": verdict.kind},
                            )
                        turn.grounding_retries += 1
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append(_grounding_correction(final_id, verdict.reason, turn.lang))
                        continue
                    # Supervisor (decisión 079): después de los controles fijos, un modelo barato
                    # revisa la respuesta con datos, políticas o acciones antes de enviarla.
                    if (
                        chat is None
                        and self._supervisor_on
                        and supervisor.needs_review(final_input, bool(turn.decisions))
                    ):
                        review = self._supervise(turn, said)
                        if review.verdict == supervisor.REWRITE and turn.supervisor_retries == 0:
                            self._audit(
                                turn,
                                "supervisor_rejected",
                                {"motivo": review.reason, "rejected_message": final_input},
                            )
                            turn.supervisor_retries += 1
                            messages.append({"role": "assistant", "content": response.content})
                            messages.append(
                                _correction(
                                    final_id, supervisor.correction(review.reason, turn.lang)
                                )
                            )
                            continue
                        if review.verdict != supervisor.APPROVE:
                            return self._give_up(
                                turn,
                                f"Supervisor: {review.reason or review.verdict}.",
                                chat,
                                {"tipo": "supervisor", "detalle": review.verdict},
                            )
                if final_name == "derivar" and chat is not None:
                    raise _ChatFallback  # en la charla no se deriva
                if (
                    final_name == "derivar"
                    and turn.handoff_retries == 0
                    and _check(self._has_tool_data, turn)
                ):
                    # Derivó con los datos de una herramienta ya en la mano (decisión 045): se
                    # le pide responder con ellos, una sola vez.
                    self._audit(
                        turn,
                        "handoff_rejected",
                        {"reason": HANDOFF_REJECTED_REASON, "rejected_handoff": final_input},
                    )
                    turn.handoff_retries += 1
                    if response.tool_calls or response.text.strip():
                        messages.append({"role": "assistant", "content": response.content})
                    messages.append(_correction(final_id, HANDOFF_CORRECTIONS[turn.lang]))
                    continue
                return self._final_action(turn, final_name, final_input)

            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                if call.name in prompt.FINAL_TOOL_NAMES:
                    outcome: dict[str, Any] = {
                        "error": "Run the tools first; answer afterwards."
                        if turn.lang == "en"
                        else "Primero se ejecutan las herramientas; respondé después."
                    }
                else:
                    if call.name == CANCEL_PENDING and CANCEL_PENDING in allowed:
                        decision = self._guardian.cancel_pending(
                            turn.tenant_id, turn.case_id, call.input
                        )
                    elif call.name in turn.retained:
                        # Retención (decisión 062): las alternativas se acaban de ofrecer; la
                        # acción espera a que el cliente conteste, no se repite en este turno.
                        decision = GuardDecision(
                            "denied",
                            {"error": RETENTION_WAIT[turn.lang]},
                            reason=RETENTION_WAIT["es"],
                        )
                    elif call.name in turn.alternatives:
                        # Retención (decisión 071): las alternativas recién ofrecidas se ofrecen
                        # con palabras; se hacen solo si el cliente las acepta. El modelo real
                        # daba el cupón aunque el cliente acababa de decir "no, gracias".
                        decision = GuardDecision(
                            "denied",
                            {"error": RETENTION_OFFER_ONLY[turn.lang]},
                            reason=RETENTION_OFFER_ONLY["es"],
                        )
                    elif call.name == upsell.OFFER_PRODUCT and call.name in allowed:
                        decision = self._guardian.offer_product(
                            tenant_id=turn.tenant_id,
                            case_id=turn.case_id,
                            message=turn.text,
                            tool_input=call.input,
                        )
                    else:
                        decision = self._guardian.execute(
                            tenant_id=turn.tenant_id,
                            case_id=turn.case_id,
                            contact=turn.contact,
                            tool_name=call.name,
                            tool_input=call.input,
                            allowed=allowed,
                        )
                    if decision.reason == RETENTION_REASON and self._offered_before(turn):
                        # El agente ya ofreció la alternativa en su mensaje anterior y el cliente
                        # insiste (decisión 071): la oferta queda hecha y la acción sigue, sin
                        # ofrecerla dos veces. El guardián controla todo lo demás.
                        self._audit(turn, "retention_already_offered", {"tool": call.name})
                        # La oferta de retención queda registrada, y después la acción.
                        turn.decisions.append(decision)
                        turn.tool_calls.append(
                            ToolExecution(call.name, call.input, decision.status, decision.reason)
                        )
                        decision = self._guardian.execute(
                            tenant_id=turn.tenant_id,
                            case_id=turn.case_id,
                            contact=turn.contact,
                            tool_name=call.name,
                            tool_input=call.input,
                            allowed=allowed,
                        )
                    if decision.reason == RETENTION_REASON:
                        turn.retained.add(call.name)
                        turn.alternatives |= {
                            str(a.get("herramienta"))
                            for a in decision.result.get("alternativas") or []
                        }
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
                            turn, "responder", turn.say("verification"), verification=True
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
                        return self._finish(turn, "responder", turn.say("duplicate_approval"))
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
        return self._give_up(
            turn,
            f"Se alcanzó el límite de {self._max_steps} pasos.",
            chat,
            {"tipo": "limite_pasos"},
        )

    def _retention_note(self, turn: _Turn, tool_names: list[str]) -> str:
        """Retención (decisión 062): en un turno posterior el modelo no ve la respuesta del
        guardián, solo el historial. Con gpt-oss-20b, cuando el cliente insistía, volvía a
        ofrecer, derivaba o afirmaba el reembolso sin hacerlo (Hito QA, decisión 066). Se le
        dice qué ya se ofreció; el guardián controla igual."""
        with tenant_scope(self._conn, turn.tenant_id):
            rows = self._conn.execute(
                "select tool, resource_key from retention_offers where case_id = %s"
                " order by created_at",
                (turn.case_id,),
            ).fetchall()
        offered = [(str(t), str(k).replace("order_id=", "")) for t, k in rows if t in tool_names]
        if not offered:
            return ""
        if turn.lang == "en":
            lines = [
                f"- The policy alternatives for {tool} on order {order} were already offered in "
                f"this conversation. Don't offer them again: if the customer still wants it, "
                f"call {tool} now."
                for tool, order in offered
            ]
            return "already_offered:\n" + "\n".join(lines)
        lines = [
            f"- Las alternativas de la política para {tool} del pedido {order} ya se ofrecieron "
            f"en esta conversación. No las vuelvas a ofrecer: si el cliente lo sigue queriendo, "
            f"llamá {tool} ahora."
            for tool, order in offered
        ]
        return "ya_ofrecido:\n" + "\n".join(lines)

    @staticmethod
    def _has_tool_data(turn: _Turn) -> bool:
        """En este turno una herramienta se ejecutó y devolvió datos (sin error), y no hay una
        aprobación creada (de eso se ocupa `_approval_pending`)."""
        if any(d.status == "approval_required" for d in turn.decisions):
            return False
        return any(d.status == "executed" and "error" not in d.result for d in turn.decisions)

    @staticmethod
    def _acted(turn: _Turn) -> bool:
        """En este turno una herramienta modificó algo o pidió una aprobación."""
        return any(
            d.side_effect and d.status in ("executed", "approval_required") for d in turn.decisions
        )

    @staticmethod
    def _executed(turn: _Turn) -> bool:
        """En este turno una herramienta modificó algo con éxito (no solo pidió aprobación)."""
        return any(d.side_effect and d.status == "executed" for d in turn.decisions)

    def _asks_personal_data(self, turn: _Turn, data: dict[str, Any]) -> bool:
        """Sin verificar, el modelo le pide al cliente el email, el teléfono u otro dato personal
        en texto: eso no verifica a nadie."""
        message = str(data.get("mensaje") or "")
        return (
            not turn.contact.verified
            and turn.needs_identity
            and PERSONAL_DATA[turn.lang].search(message) is not None
        )

    def _reverifies(self, turn: _Turn, data: dict[str, Any]) -> bool:
        """Ya verificado, el modelo habla de enviar un código o de verificar la cuenta."""
        message = str(data.get("mensaje") or "")
        return (
            turn.contact.verified
            and turn.needs_identity
            and VERIFICATION_TALKS[turn.lang].search(message) is not None
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
        return self._finish(turn, "responder", turn.say("verification"), verification=True)

    def _give_up(
        self, turn: _Turn, reason: str, chat: str | None, cause: dict[str, Any]
    ) -> AgentResult:
        """Sin una respuesta válida: deriva, salvo en la charla (mensaje fijo, sin derivar) o si
        el guardián ya creó una aprobación (el pedido queda en revisión, con mensaje fijo)."""
        if chat is not None:
            raise _ChatFallback
        approval = self._approval_pending(turn, reason)
        if approval is not None:
            return approval
        return self._escalate(turn, reason, cause)

    def _approval_pending(self, turn: _Turn, reason: str) -> AgentResult | None:
        """Si en este turno el guardián creó una aprobación, el resultado es el pedido de
        aprobación, con un mensaje fijo, sin importar qué haya hecho el modelo después: la
        decisión ya está en manos de una persona y derivar dejaría la aprobación colgando."""
        pending = next((d for d in turn.decisions if d.status == "approval_required"), None)
        if pending is None:
            return None
        self._audit(turn, "approval_message_fixed", {"reason": reason})
        return self._finish(
            turn, "pedir_aprobacion", turn.say("approval_pending"), approval_id=pending.approval_id
        )

    def _offered_before(self, turn: _Turn) -> bool:
        """El mensaje anterior del agente ya ofreció una alternativa (cupón, crédito, cambio) y
        el cliente insiste en lo que pidió."""
        if not conduct.insists(turn.text):
            return False
        history = self._history(turn.tenant_id, turn.case_id)
        previous = next((t for role, t in reversed(history) if role == "asistente"), "")
        return conduct.offers_alternative(previous)

    def _sales_correction(self, turn: _Turn, said: str) -> str | None:
        executed = [d for d in turn.decisions if d.status == "executed"]
        return conduct.sales_correction(
            said,
            [d.result for d in executed],
            # El carrito armado devuelve un link de compra.
            cart_built=any("http" in json.dumps(d.result, default=str) for d in executed),
            offers=turn.offers,
            offered=any(c.name == upsell.OFFER_PRODUCT for c in turn.tool_calls),
            language=turn.lang,
        )

    def _supervise(self, turn: _Turn, answer: str) -> supervisor.Review:
        """Revisión del supervisor (decisión 079), con su costo y su latencia en la auditoría."""
        documents: dict[str, str] = {}
        for doc in turn.documents:
            documents[doc.source] = f"{documents.get(doc.source, '')}\n{doc.text}".strip()
        results = [
            {"herramienta": call.name, "estado": decision.status, "resultado": decision.result}
            for call, decision in zip(turn.tool_calls, turn.decisions, strict=False)
        ]
        found = supervisor.review(
            self._llm,
            tenant_id=turn.tenant_id,
            case_id=turn.case_id,
            language=turn.lang,
            message=turn.text,
            answer=answer,
            documents=documents,
            tool_results=results,
            rules=turn.rules,
            tone=prompt.TONES.get(turn.tone, prompt.TONES["rioplatense"]),
        )
        if found.response is not None:
            self._audit_model_call(turn, supervisor.ROLE, found.response)
        self._audit(turn, "supervisor_review", found.audit())
        if found.error:
            self._audit(turn, "supervisor_unavailable", {"error": found.error})
        return found

    def _check_grounding(self, turn: _Turn, data: dict[str, Any]) -> GroundingVerdict:
        documents: dict[str, str] = {}
        for doc in turn.documents:
            documents[doc.source] = f"{documents.get(doc.source, '')}\n{doc.text}"
        results = [json.dumps(d.result, ensure_ascii=False) for d in turn.decisions]
        if turn.sales:
            # Precio y stock, solo de una herramienta (decisión 059).
            sold = check_sales(str(data.get("mensaje") or ""), results, documents, turn.lang)
            if not sold.ok:
                return sold
        # La memoria del cliente (decisión 078) respalda lo que dice, como su propio mensaje.
        evidence = [turn.text, *([turn.memory] if turn.memory else []), *results]
        return check_grounding(
            str(data.get("mensaje") or ""),
            [str(s) for s in data.get("fuentes") or []],
            bool(data.get("factual", True)),
            documents,
            evidence,
            tool_backed=bool(turn.decisions),
            tool_names={t.name for t in turn.tool_calls},
            language=turn.lang,
        )

    def _final_action(self, turn: _Turn, name: str, data: dict[str, Any]) -> AgentResult:
        if name == "derivar":
            reason = str(data.get("motivo") or "Derivado por el agente.")
            cause = {"tipo": "agente", "reconsiderada": turn.handoff_retries > 0}
            return self._approval_pending(turn, f"Quiso derivar: {reason}") or self._escalate(
                turn, reason, cause
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
    def _escalate(self, turn: _Turn, reason: str, cause: dict[str, Any]) -> AgentResult:
        """`cause`: por qué se derivó, uno de HANDOFF_CAUSES con su detalle (decisión 045)."""
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
        self._audit(turn, "escalated", {**summary, "causa": cause})
        message = turn.say("escalation")
        # Fuera del horario del equipo, cuándo responden (decisión 051).
        note = rules.closed_note(self._conn, turn.tenant_id, turn.lang)
        return self._finish(
            turn, "derivar", f"{message} {note}" if note else message, summary=summary
        )

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
        self._remember(turn, action)
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
    def _tenant(self, tenant_id: uuid.UUID) -> tuple[str, str, str]:
        """(nombre, tono, idioma). Una empresa en inglés habla siempre con el tono `english`."""
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select name, settings from tenants where id = %s", (tenant_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Tenant inexistente")
        settings = row[1] or {}
        lang = i18n.normalize(settings.get("language"))
        tone = str(settings.get("tone", "rioplatense"))
        if lang == "en" and not prompt.is_english(tone):
            tone = "english"
        # El nombre que eligió la empresa para el asistente (decisión 052), para el prompt.
        self._assistant = str((settings.get("brand") or {}).get("assistant") or "")
        self._supervisor_on = settings.get(supervisor.SETTING) is not False
        return row[0], tone, lang

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
        self,
        tenant_id: uuid.UUID,
        contact_id: uuid.UUID,
        company: str,
        text: str,
        lang: str = "es",
        *,
        classify_reopen: bool = True,
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
        if candidate is not None and classify_reopen and not closing.is_confirmation(text):
            case_types = self._active_case_types(tenant_id)
            classification = classify(
                self._llm,
                tenant_id=tenant_id,
                case_id=None,  # todavía no se sabe a qué caso va el mensaje
                company=company,
                message=text,
                case_types={name: goal for name, (goal, _) in case_types.items()},
                language=lang,
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

    def _current_case_type(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> str | None:
        """El tipo de caso que la conversación tiene hasta ahora (decisión 040)."""
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select t.name from cases c join case_types t on t.id = c.case_type_id"
                " where c.id = %s",
                (case_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def _has_pending(self, tenant_id: uuid.UUID, case_id: uuid.UUID) -> bool:
        """El caso tiene una solicitud esperando la aprobación de una persona."""
        with tenant_scope(self._conn, tenant_id):
            row = self._conn.execute(
                "select 1 from approvals where case_id = %s and status = 'pending' limit 1",
                (case_id,),
            ).fetchone()
        return row is not None

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
