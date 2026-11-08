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
) -> GroundingVerdict:
    invalid = [s for s in sources if s not in documents]
    if invalid:
        return GroundingVerdict(False, f"cita fuentes que no estaban en los documentos: {invalid}")
    if factual and not sources and not tool_backed:
        return GroundingVerdict(False, "afirmación factual sin fuente ni herramienta")
    known = set(_NUMBER.findall(" ".join([*documents.values(), *evidence])))
    unsupported = sorted({n for n in _NUMBER.findall(message) if n not in known})
    if unsupported:
        return GroundingVerdict(False, f"datos sin respaldo en las fuentes: {unsupported}")
    return GroundingVerdict(True)
