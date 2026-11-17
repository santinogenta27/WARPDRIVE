"""Armado del prompt del agente (estructura de la spec v1.1, "Estructura del prompt")."""

import copy
import html
from pathlib import Path
from typing import Any

from app.policy.guardian import CANCEL_PENDING
from app.rag.search import SearchResult

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# v2: cuándo derivar y cuándo explicar (docs/calidad.md). v3: sin verificar, usar la herramienta
# (dispara la verificación por código) y nunca pedir datos personales en texto (decisión 024).
# v4: pedir el dato que falta en lugar de derivar, y responder con lo que los documentos dicen
# del tema aunque no nombren lo puntual (decisión 027).
# v5: consultar el pedido antes de modificarlo, explicar las reglas del negocio con una
# alternativa en lugar de derivar, y un mensaje sin JSON ni metadatos (docs/calidad.md).
SYSTEM_PROMPT_VERSION = "system_v5"
# Empresas en inglés (decisión 036): la traducción de la misma versión, no una versión nueva.
ENGLISH_SUFFIX = "_en"
# Reglas de conducta (decisión 040): empatía, opciones, cambio de idea, dos temas y no afirmar
# acciones que no hizo una herramienta. Se suman al prompt en inglés.
CONDUCT_VERSION = "conduct_v1_en"

TONES = {
    "rioplatense": (
        'Hablá siempre en español rioplatense con voseo: "indicanos", "decime", "pasame",'
        ' "podés", "querés", "tenés" ("¿Me pasás el número de pedido?"). Nunca uses "tú" ni'
        ' "usted" ni sus formas ("indícanos", "dime", "puedes").'
    ),
    "neutro": "Hablá en español neutro con tuteo.",
    "english": (
        "Always write in natural, warm and professional English, like a friendly support "
        "specialist at a good online store: clear, courteous and to the point. Use contractions "
        '("I\'ll", "you\'re"), address the customer as "you", and avoid stiff phrases, slang '
        "and emojis. Reply in English even if the customer writes in another language."
    ),
    "ingles": "Answer in English.",
}
ENGLISH_TONES = {"english", "ingles"}


def is_english(tone: str) -> bool:
    return tone in ENGLISH_TONES


FINAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "responder",
        "description": "Envía la respuesta final al cliente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mensaje": {"type": "string", "description": "Texto para el cliente"},
                "fuentes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Id de los documentos usados (vacío si no usaste ninguno)",
                },
                "factual": {
                    "type": "boolean",
                    "description": "true si el mensaje afirma datos de la empresa o de un pedido",
                },
            },
            "required": ["mensaje", "fuentes", "factual"],
            "additionalProperties": False,
        },
    },
    {
        "name": "derivar",
        "description": "Deriva el caso a una persona del equipo con un resumen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string"},
                "resumen": {"type": "string"},
            },
            "required": ["motivo", "resumen"],
            "additionalProperties": False,
        },
    },
]
FINAL_TOOL_NAMES = {t["name"] for t in FINAL_TOOLS}
# Las mismas herramientas finales, con las descripciones en inglés: los nombres no cambian.
_ENGLISH_DESCRIPTIONS = {
    ("responder", None): "Sends the final answer to the customer.",
    ("responder", "mensaje"): "Text for the customer",
    ("responder", "fuentes"): "Ids of the documents used (empty if you used none)",
    ("responder", "factual"): "true if the message states facts about the company or an order",
    ("derivar", None): "Hands the case off to a member of the team, with a summary.",
}


def _final_tools(english: bool) -> list[dict[str, Any]]:
    if not english:
        return FINAL_TOOLS
    translated = copy.deepcopy(FINAL_TOOLS)
    for tool in translated:
        tool["description"] = _ENGLISH_DESCRIPTIONS[(tool["name"], None)]
        for field, spec in tool["input_schema"]["properties"].items():
            if (tool["name"], field) in _ENGLISH_DESCRIPTIONS:
                spec["description"] = _ENGLISH_DESCRIPTIONS[(tool["name"], field)]
    return translated


def _escape(text: str) -> str:
    """El contenido externo es dato: se escapan los signos de etiqueta para que no cierre tags."""
    return html.escape(text, quote=False)


def conduct_rules() -> str:
    """Reglas de conducta del Hito 8 (decisión 040), aparte de la traducción del prompt: por ahora
    solo en inglés, así el prompt en español y la línea base de Acme no cambian."""
    return (PROMPTS_DIR / f"{CONDUCT_VERSION}.txt").read_text(encoding="utf-8").strip()


def system_prompt(company: str, tone: str, case_type_instructions: str, assistant: str = "") -> str:
    name = SYSTEM_PROMPT_VERSION + (ENGLISH_SUFFIX if is_english(tone) else "")
    template = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    fixed = template.replace("{empresa}", company).replace(
        "{tono}", TONES.get(tone, TONES["rioplatense"])
    )
    if is_english(tone):
        fixed = f"{fixed.rstrip()}\n{conduct_rules()}"
    if assistant:  # el nombre que eligió la empresa (decisión 052); sin nombre, no cambia nada
        intro = (
            f"Your name is {_escape(assistant)}: introduce yourself with it."
            if is_english(tone)
            else f"Te llamás {_escape(assistant)}: presentate con ese nombre."
        )
        fixed = f"{fixed.rstrip()}\n{intro}"
    return f"{fixed}\n<tipo_de_caso>\n{case_type_instructions.strip()}\n</tipo_de_caso>"


def user_turn(
    documents: list[SearchResult], verified: bool, history: list[tuple[str, str]], message: str
) -> str:
    docs = "\n".join(
        f'<documento id="{d.source}">\n{_escape(d.text)}\n</documento>' for d in documents
    )
    past = "\n".join(f"{role}: {_escape(text)}" for role, text in history[-20:])
    return (
        f"<documentos>\n{docs}\n</documentos>\n"
        f'<contacto verificado="{"sí" if verified else "no"}"/>\n'
        f"<historial>\n{past}\n</historial>\n"
        f"<mensaje_cliente>{_escape(message)}</mensaje_cliente>"
    )


def cancel_pending_tool(english: bool) -> dict[str, Any]:
    """Herramienta interna para un cambio de idea (decisión 040): cancela lo que el caso tiene
    pendiente de aprobación. La ejecuta el guardián; se ofrece solo si hay algo pendiente."""
    return {
        "name": CANCEL_PENDING,
        "description": (
            "Cancels a request of this conversation that is still waiting for a person's "
            "approval, when the customer changes their mind. Use it before doing something else "
            "with the same order."
            if english
            else "Cancela una solicitud de esta conversación que espera la aprobación de una "
            "persona, cuando el cliente cambia de idea. Usala antes de hacer otra cosa con el "
            "mismo pedido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order of the pending request"
                    if english
                    else "El pedido de la solicitud pendiente",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    }


def tool_definitions(
    manifests: list[dict[str, Any]],
    finals: tuple[str, ...] = ("responder", "derivar"),
    *,
    english: bool = False,
    pending: bool = False,
) -> list[dict[str, Any]]:
    """`pending`: el caso tiene algo pendiente de aprobación; se suma `cancelar_pendiente`."""
    business = [
        {"name": m["name"], "description": m["description"], "input_schema": m["input_schema"]}
        for m in manifests
    ]
    extra = [cancel_pending_tool(english)] if pending else []
    return business + extra + [t for t in _final_tools(english) if t["name"] in finals]
