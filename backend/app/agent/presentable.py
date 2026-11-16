"""Qué no se le puede mostrar a un cliente: una respuesta vacía, JSON o metadatos del agente.

Con gpt-oss-120b, después de usar una herramienta, el modelo escribía como texto suelto el JSON
que tenía que pasarle a `responder` ({"accion": ..., "fuentes": [...], "mensaje": ...}) o
agregaba "Fuentes: [...] factual: true" al final, y eso llegaba tal cual al cliente
(docs/calidad.md). Lo usan el agente (no lo manda) y los evals (no lo dan por resuelto).
"""

import json
import re
from typing import Any

_CHECKS = [
    ("JSON", re.compile(r'^\s*(?:```|[{\[])|"\w+"\s*:')),
    # En español y en inglés (decisión 036): "Fuentes: [...]", "Sources: [...] action: ...".
    ("metadatos", re.compile(r"(?i)\b(?:fuentes|factual|acci[oó]n|sources|action)\s*:")),
    ("marcas del modelo", re.compile(r"<\|\w+\|>")),
    # Nombres de herramientas (consultar_pedido): el texto para el cliente no tiene guiones bajos
    # fuera de un email.
    ("nombre de herramienta", re.compile(r"(?<![\w.@])[a-z]+(?:_[a-z]+)+(?![\w@])")),
]


EMPTY = "respuesta vacía"
ISSUES = (EMPTY, *(name for name, _ in _CHECKS))  # lo que puede devolver `problem`


def problem(message: str) -> str | None:
    """Por qué el mensaje no se le puede mandar al cliente, o None si se puede."""
    if not message.strip():
        return EMPTY
    for name, pattern in _CHECKS:
        if pattern.search(message):
            return name
    return None


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_loose(text: str) -> dict[str, Any] | None:
    """El JSON que el modelo escribió como texto en lugar de usar la herramienta final, si trae
    un mensaje para el cliente: {"mensaje", "fuentes", "factual"} o {"accion": "derivar", ...}."""
    try:
        data = json.loads(_FENCE.sub("", text))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("accion") == "derivar":
        return {"accion": "derivar", "motivo": str(data.get("motivo") or "Derivado por el agente.")}
    message = data.get("mensaje")
    if not isinstance(message, str) or problem(message) is not None:
        return None
    sources = data.get("fuentes")
    return {
        "mensaje": message,
        "fuentes": [str(s) for s in sources] if isinstance(sources, list) else [],
        "factual": bool(data.get("factual", True)),
    }
