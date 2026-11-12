"""Datos sensibles en los documentos del onboarding (decisión 035).

Un documento es una política de la empresa: sus emails, teléfonos y CUIT son parte de lo que el
asistente responde, así que no se tocan (decisión 030). Lo que nunca hace falta en una política
es un número de tarjeta o un DNI: esos se reemplazan por marcadores antes de guardar.

Además, si el documento trae varios emails o teléfonos distintos, parece una lista de personas y
no una política: se avisa y se pide confirmación antes de indexarlo.
"""

import re

from app.onboarding.anonymize import CARD, DNI_DOTTED, DNI_LABELED, EMAIL, PHONE, luhn_ok

# Una empresa suele tener una o dos casillas y una o dos líneas. Desde tres distintos, el
# documento parece una lista de personas.
MANY = 3
# "$ 28.456.789" o "12.500.000 pesos" son montos, no documentos de identidad.
_MONEY_BEFORE = re.compile(r"(?:\$|\b(?:ars|usd|u\$s))\s*$", re.IGNORECASE)
_MONEY_AFTER = re.compile(r"^(?:,\d{1,2}\b|\s*(?:pesos|d[oó]lares|ars|usd)\b)", re.IGNORECASE)


def redact(text: str) -> tuple[str, dict[str, int]]:
    """El texto sin números de tarjeta ni DNI, y cuántos distintos se reemplazaron. El mismo
    número recibe siempre el mismo marcador."""
    markers: dict[tuple[str, str], str] = {}

    def marker(kind: str, digits: str) -> str:
        if (kind, digits) not in markers:
            count = sum(1 for k, _ in markers if k == kind) + 1
            markers[(kind, digits)] = f"<{kind}_{count}>"
        return markers[(kind, digits)]

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return marker("CARD", digits) if luhn_ok(digits) else match.group(0)

    def dotted(match: re.Match[str]) -> str:
        before, after = match.string[: match.start()], match.string[match.end() :]
        if _MONEY_BEFORE.search(before) or _MONEY_AFTER.match(after):
            return match.group(0)
        return marker("DNI", re.sub(r"\D", "", match.group(0)))

    text = CARD.sub(card, text)
    text = DNI_DOTTED.sub(dotted, text)
    text = DNI_LABELED.sub(lambda m: m.group(1) + marker("DNI", m.group(2)), text)
    found = {
        "tarjetas": sum(1 for kind, _ in markers if kind == "CARD"),
        "dni": sum(1 for kind, _ in markers if kind == "DNI"),
    }
    return text, found


def personal_data(text: str) -> str | None:
    """Por qué el documento parece tener datos de varias personas ("4 emails y 4 teléfonos
    distintos"), o None si parece una política normal."""
    emails = {m.group(0).lower() for m in EMAIL.finditer(text)}
    phones = set()
    for match in PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 10:
            phones.add(digits[-8:])  # el mismo número con o sin 54, 9 o 15
    parts = []
    if len(emails) >= MANY:
        parts.append(f"{len(emails)} emails")
    if len(phones) >= MANY:
        parts.append(f"{len(phones)} teléfonos")
    return " y ".join(parts) + " distintos" if parts else None
