"""Identidad de contactos. El teléfono se guarda como hash; nunca en claro."""

import hashlib
import re


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"+{digits}"


def hash_phone(phone: str) -> str:
    return hashlib.sha256(normalize_phone(phone).encode()).hexdigest()
