"""Control de fundamento: toda afirmación factual tiene fuente o sale de una herramienta.

Controles en código (no dependen de que el modelo se autoevalúe bien):
  1. Las fuentes citadas existen entre los documentos que recibió (no puede inventar fuentes).
  2. Si la respuesta se declara factual, cita al menos un documento o hubo herramientas.
  3. Cada número de la respuesta (fechas, montos, plazos) aparece en la evidencia: documentos,
     resultados de herramientas o el propio mensaje del cliente. Esto atrapa datos inventados
     aunque el modelo no marque la respuesta como factual.
  4. Una fecha escrita con el mes ("November 14", "Nov 14", "14 de noviembre") tiene ese día y
     ese mes en la evidencia (decisión 036): "December 14" no tiene respaldo en 2026-11-14.
Lo que no cubre (relevancia de la cita, afirmaciones sin números) se mide con el juez de evals.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d+")
# Montos con separador de miles ("$35.000", "1.500.000"): son el número entero (35000), como lo
# devuelve una herramienta. Sin esto, "35.000" se leía como "35" y "000" y no tenía respaldo.
_THOUSANDS = re.compile(r"\b\d{1,3}(?:\.\d{3})+\b(?!,\d)")
# En inglés la coma separa miles y el punto, decimales: "$1,250.00" es 1250.
_THOUSANDS_EN = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
_DECIMAL = re.compile(r"\d+(?:\.\d+)?")
# El número de una lista ("1. A 10% coupon", "2) Free express shipping") no es un dato: lo vio
# gpt-oss-20b al ofrecer dos opciones y la respuesta terminaba derivada (decisión 040).
_LIST_MARKER = re.compile(r"(?m)^[ \t]*\d{1,2}[.)][ \t]+")


def _decimal(token: str) -> str:
    """ "120.00" -> "120", "120.50" -> "120.5": el mismo monto, escriba como lo escriba."""
    try:
        return format(Decimal(token).normalize(), "f")
    except InvalidOperation:
        return token


def _numbers(text: str, language: str = "es") -> list[str]:
    if language == "en":
        joined = _THOUSANDS_EN.sub(lambda m: m.group(0).replace(",", ""), text)
        return [_decimal(n) for n in _DECIMAL.findall(joined)]
    joined = _THOUSANDS.sub(lambda m: m.group(0).replace(".", ""), text)
    return _NUMBER.findall(joined)


_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan", "enero"),
            ("february", "feb", "febrero"),
            ("march", "mar", "marzo"),
            ("april", "apr", "abril"),
            ("may", "mayo"),
            ("june", "jun", "junio"),
            ("july", "jul", "julio"),
            ("august", "aug", "agosto"),
            ("september", "sept", "sep", "septiembre", "setiembre"),
            ("october", "oct", "octubre"),
            ("november", "nov", "noviembre"),
            ("december", "dec", "diciembre"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
# "November 14", "Nov. 14th", "Wednesday, November 14"
_MONTH_DAY = re.compile(rf"(?i)\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")
# "14 November", "14th of November", "14 de noviembre"
_DAY_MONTH = re.compile(rf"(?i)\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+|de\s+)?({_MONTH})\b\.?")
_ISO = re.compile(r"\b\d{4}-(\d{2})-(\d{2})")
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


def _written_dates(text: str) -> set[tuple[int, int]]:
    """(mes, día) de las fechas escritas con el nombre del mes."""
    found = {(_MONTHS[m.group(1).lower()], int(m.group(2))) for m in _MONTH_DAY.finditer(text)}
    found |= {(_MONTHS[m.group(2).lower()], int(m.group(1))) for m in _DAY_MONTH.finditer(text)}
    return {(month, day) for month, day in found if 1 <= day <= 31}


def _evidence_dates(text: str) -> set[tuple[int, int]]:
    """(mes, día) de todas las fechas de la evidencia: ISO, escritas, y "14/11" o "11/14"."""
    found = {(int(m.group(1)), int(m.group(2))) for m in _ISO.finditer(text)}
    for m in _SLASH.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        found |= {(b, a), (a, b)}
    return found | _written_dates(text)


# "20:00" dice la hora 20, como "20 horas" en un documento: los minutos en punto no son un dato.
_ON_THE_HOUR = re.compile(r"\b(\d{1,2}):00\b")


def _plain(number: str) -> str:
    """Sin ceros adelante: "9:30" y "09:30", o el día 5 y "2026-11-05", son el mismo dato."""
    return number.lstrip("0") or "0"


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
    language: str = "es",
) -> GroundingVerdict:
    """`tool_names`: herramientas ejecutadas en el turno. Si el modelo cita una como fuente, no
    es una fuente inventada: el dato sale de su resultado, que ya está en `evidence`. Con una
    herramienta detrás, cualquier "fuente" que no sea un documento (un número de pedido) se
    ignora; los números de la respuesta se siguen controlando contra la evidencia. `language`:
    cómo se escriben los montos (decisión 036)."""
    sources = [s for s in sources if s not in tool_names]
    if tool_backed:
        sources = [s for s in sources if s in documents]
    invalid = [s for s in sources if s not in documents]
    if invalid:
        return GroundingVerdict(False, f"cita fuentes que no estaban en los documentos: {invalid}")
    if factual and not sources and not tool_backed:
        return GroundingVerdict(False, "afirmación factual sin fuente ni herramienta")
    sources_text = " ".join([*documents.values(), *evidence])
    known = {
        _plain(n)
        for n in [
            *_NUMBER.findall(sources_text),
            *_numbers(sources_text),
            *_numbers(sources_text, "en"),
        ]
    }
    numbers = _numbers(_ON_THE_HOUR.sub(r"\1", _LIST_MARKER.sub("", message)), language)
    unsupported = sorted({n for n in numbers if _plain(n) not in known})
    if unsupported:
        return GroundingVerdict(False, f"datos sin respaldo en las fuentes: {unsupported}")
    dates = _written_dates(message) - _evidence_dates(sources_text)
    if dates:
        shown = [f"{day:02d}/{month:02d}" for month, day in sorted(dates)]
        return GroundingVerdict(False, f"fechas sin respaldo en las fuentes: {shown}")
    return GroundingVerdict(True)
