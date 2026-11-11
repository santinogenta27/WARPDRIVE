"""Control de fundamento: toda afirmación factual tiene fuente o sale de una herramienta.

Controles en código (no dependen de que el modelo se autoevalúe bien):
  1. Las fuentes citadas existen entre los documentos que recibió (no puede inventar fuentes).
  2. Si la respuesta se declara factual, cita al menos un documento o hubo herramientas.
  3. Cada número de la respuesta (fechas, montos, plazos) aparece en la evidencia: documentos,
     resultados de herramientas o el propio mensaje del cliente. Esto atrapa datos inventados
     aunque el modelo no marque la respuesta como factual.
Lo que no cubre (relevancia de la cita, afirmaciones sin números) se mide con el juez de evals.
"""

import re
from dataclasses import dataclass

_NUMBER = re.compile(r"\d+")
# Montos con separador de miles ("$35.000", "1.500.000"): son el número entero (35000), como lo
# devuelve una herramienta. Sin esto, "35.000" se leía como "35" y "000" y no tenía respaldo.
_THOUSANDS = re.compile(r"\b\d{1,3}(?:\.\d{3})+\b(?!,\d)")


def _numbers(text: str) -> list[str]:
    joined = _THOUSANDS.sub(lambda m: m.group(0).replace(".", ""), text)
    return _NUMBER.findall(joined)


# "20:00" dice la hora 20, como "20 horas" en un documento: los minutos en punto no son un dato.
_ON_THE_HOUR = re.compile(r"\b(\d{1,2}):00\b")


@dataclass(frozen=True)
class GroundingVerdict:
    ok: bool
    reason: str = ""


def check_grounding(
    message: str,
    sources: list[str],
    factual: bool,
    documents: dict[str, str],
    evidence: list[str],
    tool_backed: bool,
    tool_names: set[str] | frozenset[str] = frozenset(),
) -> GroundingVerdict:
    """`tool_names`: herramientas ejecutadas en el turno. Si el modelo cita una como fuente, no
    es una fuente inventada: el dato sale de su resultado, que ya está en `evidence`. Con una
    herramienta detrás, cualquier "fuente" que no sea un documento (un número de pedido) se
    ignora; los números de la respuesta se siguen controlando contra la evidencia."""
    sources = [s for s in sources if s not in tool_names]
    if tool_backed:
        sources = [s for s in sources if s in documents]
    invalid = [s for s in sources if s not in documents]
    if invalid:
        return GroundingVerdict(False, f"cita fuentes que no estaban en los documentos: {invalid}")
    if factual and not sources and not tool_backed:
        return GroundingVerdict(False, "afirmación factual sin fuente ni herramienta")
    sources_text = " ".join([*documents.values(), *evidence])
    known = set(_NUMBER.findall(sources_text)) | set(_numbers(sources_text))
    numbers = _numbers(_ON_THE_HOUR.sub(r"\1", message))
    unsupported = sorted({n for n in numbers if n not in known})
    if unsupported:
        return GroundingVerdict(False, f"datos sin respaldo en las fuentes: {unsupported}")
    return GroundingVerdict(True)
