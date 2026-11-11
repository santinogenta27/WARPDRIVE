"""Tarea 15 — anonimización antes de que cualquier dato llegue a un modelo (spec, etapa 2).

Reemplaza emails, tarjetas (validadas con Luhn), CUIT, DNI, teléfonos y nombres por marcadores
tipados (`<PHONE_1>`). Un `Anonymizer` por conversación: el mismo dato recibe siempre el mismo
marcador, así el texto sigue teniendo sentido ("<NAME_1> pidió..., <NAME_1> confirmó...").

Nombres: los de los participantes que trae la fuente (autor del chat, solicitante del ticket) y
los que el cliente da al presentarse ("soy X", "me llamo X"). Se conservan números de pedido,
montos y fechas, que hacen falta para descubrir casos y generar evals.
"""

import re
import unicodedata
from collections import defaultdict

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
CUIT = re.compile(r"\b(?:20|23|24|27|30|33|34)-?\d{8}-?\d\b")
DNI_DOTTED = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}\b")
DNI_LABELED = re.compile(r"(?i)(\bdni\s*(?:n[°ºo]\.?\s*)?:?\s*(?:es\s*)?)(\d{7,8})\b")
PHONE = re.compile(
    r"(?<![\w<])(?:\+?54[ -]?)?(?:9[ -]?)?(?:\(?0?(?:11|[23]\d{2,3})\)?[ -]?)(?:15[ -]?)?"
    r"\d{3,4}[ -]?\d{4}(?![\d>])"
)
WORD = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
INTRODUCTION = re.compile(rf"(\b(?:[Ss]oy|[Mm]e llamo|[Mm]i nombre es)\s+)({WORD}(?:\s+{WORD})?)")
NOT_NAMES = {"Cliente", "Clienta", "Yo", "El", "La", "De", "Del", "Un", "Una"}


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        value = int(ch)
        if i % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return len(digits) >= 13 and total % 10 == 0


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


class Anonymizer:
    def __init__(self, names: list[str] | None = None) -> None:
        self._markers: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = defaultdict(int)
        self._names: dict[str, str] = {}  # forma del nombre (con y sin tildes) -> marcador
        for name in names or []:
            self._register_name(name)

    def _marker(self, kind: str, key: str) -> str:
        if (kind, key) not in self._markers:
            self._counts[kind] += 1
            self._markers[(kind, key)] = f"<{kind}_{self._counts[kind]}>"
        return self._markers[(kind, key)]

    def _register_name(self, name: str) -> str:
        parts = [p for p in name.split() if p not in NOT_NAMES and len(p) > 1]
        if not parts:
            return name
        marker = self._marker("NAME", _plain(" ".join(parts)).lower())
        for form in {" ".join(parts), *parts}:
            for variant in {form, _plain(form)}:
                self._names.setdefault(variant, marker)
        return marker

    def anonymize(self, text: str) -> str:
        text = EMAIL.sub(lambda m: self._marker("EMAIL", m.group(0).lower()), text)
        text = CARD.sub(self._card, text)
        text = CUIT.sub(lambda m: self._marker("CUIT", re.sub(r"\D", "", m.group(0))), text)
        text = DNI_DOTTED.sub(lambda m: self._marker("DNI", re.sub(r"\D", "", m.group(0))), text)
        text = DNI_LABELED.sub(lambda m: m.group(1) + self._marker("DNI", m.group(2)), text)
        text = PHONE.sub(self._phone, text)
        text = INTRODUCTION.sub(lambda m: m.group(1) + self._register_name(m.group(2)), text)
        for form in sorted(self._names, key=len, reverse=True):  # el nombre completo primero
            text = re.sub(rf"(?<![\w<]){re.escape(form)}(?!\w)", self._names[form], text)
        return text

    def _card(self, match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return self._marker("CARD", digits) if luhn_ok(digits) else match.group(0)

    def _phone(self, match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 10:
            return match.group(0)
        return self._marker("PHONE", digits[-8:])  # mismo número con o sin 54, 9 o 15
