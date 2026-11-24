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
from typing import Any

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
# "8 p.m." es la hora 20 (decisión 093): en inglés, la respuesta pasa a 12 horas lo que el
# documento dice en 24 ("de 10 a 20 hs"), o al revés.
_TWELVE_HOUR = re.compile(r"(?i)\b(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?\s?m\b\.?")


# "24/7" dice "las 24 horas, todos los días": el 7 tiene respaldo si las fuentes lo dicen así.
_ALWAYS = re.compile(r"\b24\s*/\s*7\b")
_EVERY_DAY = re.compile(r"(?i)todos\s+los\s+d[ií]as|every\s*day|7\s+d[ií]as|7\s+days|24/7")


def _to_24h(text: str) -> str:
    def convert(found: re.Match[str]) -> str:
        hour = int(found.group(1))
        if not 1 <= hour <= 12:
            return found.group(0)
        hour = hour % 12 + (12 if found.group(3).lower() == "p" else 0)
        return f"{hour}:{found.group(2)}" if found.group(2) else str(hour)

    return _TWELVE_HOUR.sub(convert, text)


def _plain(number: str) -> str:
    """Sin ceros adelante: "9:30" y "09:30", o el día 5 y "2026-11-05", son el mismo dato."""
    return number.lstrip("0") or "0"


# Qué control rechazó la respuesta, sin los datos (decisión 045): lo muestra "Behind the scenes".
INVALID_SOURCE, NO_SOURCE, NUMBER, DATE = "fuente_inexistente", "sin_fuente", "numero", "fecha"
PRICE, STOCK, LINK = "precio", "stock", "link"  # ventas (decisiones 059 y 060)
KINDS = (INVALID_SOURCE, NO_SOURCE, NUMBER, DATE, PRICE, STOCK, LINK)


@dataclass(frozen=True)
class GroundingVerdict:
    ok: bool
    reason: str = ""
    kind: str = ""  # uno de KINDS si no pasó


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
        return GroundingVerdict(
            False, f"cita fuentes que no estaban en los documentos: {invalid}", INVALID_SOURCE
        )
    if factual and not sources and not tool_backed:
        return GroundingVerdict(False, "afirmación factual sin fuente ni herramienta", NO_SOURCE)
    sources_text = " ".join([*documents.values(), *evidence])
    in_24h = _to_24h(sources_text)
    known = {
        _plain(n)
        for n in [
            *_NUMBER.findall(sources_text),
            *_numbers(sources_text),
            *_numbers(sources_text, "en"),
            *_NUMBER.findall(in_24h),
        ]
    }
    checked = _to_24h(_LIST_MARKER.sub("", message))
    if "24" in known and _EVERY_DAY.search(sources_text):
        checked = _ALWAYS.sub("24", checked)
    numbers = _numbers(_ON_THE_HOUR.sub(r"\1", checked), language)
    unsupported = sorted({n for n in numbers if _plain(n) not in known})
    if unsupported:
        return GroundingVerdict(False, f"datos sin respaldo en las fuentes: {unsupported}", NUMBER)
    dates = _written_dates(message) - _evidence_dates(sources_text)
    if dates:
        shown = [f"{day:02d}/{month:02d}" for month, day in sorted(dates)]
        return GroundingVerdict(False, f"fechas sin respaldo en las fuentes: {shown}", DATE)
    return GroundingVerdict(True)


# -- ventas: precio y stock solo de una herramienta (decisión 059) -----------------------------
# "$149", "$ 85.000", "US$ 20", "149 USD", "85000 pesos".
_MONEY = re.compile(
    r"(?i)(?:US)?\$\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|ARS|dollars?|d[oó]lares|pesos)\b"
)
# Un documento que es una lista de precios (el catálogo) no respalda un precio: puede estar
# viejo. Las políticas sí ("envío gratis desde $75").
PRICE_LIST = re.compile(r"(?i)catal|product|precio|price|stock")
_STOCK_CLAIM = re.compile(
    r"(?i)\b(?:in\s+stock|out\s+of\s+stock|sold\s+out|back\s+in\s+stock|units?\s+left|only\s+\d+"
    r"\s+left|we\s+(?:still\s+)?have\s+(?:it|them|one|some)|(?:is|are)\s+available\s+in"
    r"|hay\s+stock|sin\s+stock|agotad[oa]s?|nos\s+quedan?|(?:est[aá]n?|hay)\s+disponibles?)\b"
)


_URL = re.compile(r"https?://\S+")


def is_product_tool(manifest: dict[str, Any]) -> bool:
    """La búsqueda de productos de la API canónica de tienda (`GET /products`)."""
    upstream = manifest.get("upstream") or {}
    return upstream.get("method") == "GET" and str(upstream.get("path")).startswith("/products")


def check_sales(
    message: str, tool_evidence: list[str], documents: dict[str, str], language: str = "es"
) -> GroundingVerdict:
    """En un turno de ventas (con la búsqueda de productos): cada monto de la respuesta sale de
    un resultado de herramienta o de una política, nunca de una lista de precios ni del mensaje
    del cliente; y decir que hay o no hay stock exige una herramienta ejecutada en el turno."""
    allowed = " ".join(
        [
            *tool_evidence,
            *(text for source, text in documents.items() if not PRICE_LIST.search(source)),
        ]
    )
    known = {
        _plain(n) for n in [*_NUMBER.findall(allowed), *_numbers(allowed), *_numbers(allowed, "en")]
    }
    amounts = [n for token in _MONEY.findall(message) for n in _numbers(token, language)]
    unsupported = sorted({n for n in amounts if _plain(n) not in known})
    if unsupported:
        return GroundingVerdict(
            False, f"precio sin respaldo en una herramienta: {unsupported}", PRICE
        )
    if not tool_evidence and _STOCK_CLAIM.search(message):
        return GroundingVerdict(False, "afirma stock sin consultar la herramienta", STOCK)
    # Un link de compra inventado lleva al cliente a cualquier lado (decisión 060): cada link
    # sale de un resultado de herramienta o de un documento.
    linked = " ".join([*tool_evidence, *documents.values()])
    invented = [url for url in _URL.findall(message) if url.rstrip(".,;:)") not in linked]
    if invented:
        return GroundingVerdict(False, f"link que no salió de una herramienta: {invented}", LINK)
    return GroundingVerdict(True)
