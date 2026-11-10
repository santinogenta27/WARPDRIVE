"""Armado del prompt del agente (estructura de la spec v1.1, "Estructura del prompt")."""

import html
from pathlib import Path
from typing import Any

from app.rag.search import SearchResult

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# v2: cuándo derivar y cuándo explicar (docs/calidad.md). v3: sin verificar, usar la herramienta
# (dispara la verificación por código) y nunca pedir datos personales en texto (decisión 024).
SYSTEM_PROMPT_VERSION = "system_v3"

TONES = {
    "rioplatense": (
        'Hablá siempre en español rioplatense con voseo: "indicanos", "decime", "pasame",'
        ' "podés", "querés", "tenés" ("¿Me pasás el número de pedido?"). Nunca uses "tú" ni'
        ' "usted" ni sus formas ("indícanos", "dime", "puedes").'
    ),
    "neutro": "Hablá en español neutro con tuteo.",
    "ingles": "Answer in English.",
}

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


def _escape(text: str) -> str:
    """El contenido externo es dato: se escapan los signos de etiqueta para que no cierre tags."""
    return html.escape(text, quote=False)


def system_prompt(company: str, tone: str, case_type_instructions: str) -> str:
    template = (PROMPTS_DIR / f"{SYSTEM_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    fixed = template.replace("{empresa}", company).replace(
        "{tono}", TONES.get(tone, TONES["rioplatense"])
    )
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


def tool_definitions(
    manifests: list[dict[str, Any]], finals: tuple[str, ...] = ("responder", "derivar")
) -> list[dict[str, Any]]:
    business = [
        {"name": m["name"], "description": m["description"], "input_schema": m["input_schema"]}
        for m in manifests
    ]
    return business + [t for t in FINAL_TOOLS if t["name"] in finals]
