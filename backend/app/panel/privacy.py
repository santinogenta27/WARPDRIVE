"""Resúmenes de casos sin datos sensibles: nunca códigos de verificación ni datos personales."""

import re

from app.onboarding.anonymize import CARD, CUIT, DNI_DOTTED, DNI_LABELED, EMAIL, PHONE, luhn_ok

# Código de verificación: 6 dígitos sueltos (no un monto como $150.000 o 150000 pesos).
CODE = re.compile(r"(?<![\d$.,])\d{6}(?!\d|[.,]\d)(?!\s*pesos)")
NAME = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
INTRODUCTION = re.compile(rf"(\b(?:[Ss]oy|[Mm]e llamo|[Mm]i nombre es)\s+){NAME}(?:\s+{NAME})?")
PLACEHOLDER = re.compile(r"\[[a-zéóA-Z]+\]")


def _card(match: re.Match[str]) -> str:
    return "[tarjeta]" if luhn_ok(re.sub(r"\D", "", match.group(0))) else match.group(0)


def _phone(match: re.Match[str]) -> str:
    return "[teléfono]" if len(re.sub(r"\D", "", match.group(0))) >= 10 else match.group(0)


def redact(text: str) -> str:
    """Sin códigos ni datos personales: emails, tarjetas, CUIT, DNI, teléfonos y nombres."""
    text = EMAIL.sub("[email]", text)
    text = CARD.sub(_card, text)
    text = CUIT.sub("[CUIT]", text)
    text = DNI_DOTTED.sub("[DNI]", text)
    text = DNI_LABELED.sub(lambda m: m.group(1) + "[DNI]", text)
    text = PHONE.sub(_phone, text)
    text = CODE.sub("[código]", text)
    return INTRODUCTION.sub(lambda m: m.group(1) + "[nombre]", text)


def mask_codes(text: str) -> str:
    """Para el detalle del caso: el equipo ve la conversación, pero nunca un código."""
    return CODE.sub("[código]", text)


def summary(customer_messages: list[str]) -> str:
    """Resumen de un caso: el primer mensaje del cliente que dice algo (no solo su email o el
    código), sin datos sensibles."""
    redacted = [redact(m).strip() for m in customer_messages]
    for text in redacted:
        if len(PLACEHOLDER.sub("", text).split()) >= 2:
            return text
    return redacted[0] if redacted else ""
