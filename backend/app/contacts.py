"""Identidad de contactos. El teléfono se guarda como hash; nunca en claro."""

import hashlib
import re


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"+{digits}"


def hash_phone(phone: str) -> str:
    return hashlib.sha256(normalize_phone(phone).encode()).hexdigest()


WEB_PREFIX = "web:"


def hash_address(address: str) -> str:
    """Hash del contacto según su dirección: un teléfono (WhatsApp) o `web:<sesión>` (chat web,
    sin teléfono). Los dos espacios no se cruzan: el de teléfonos solo usa dígitos."""
    if address.startswith(WEB_PREFIX):
        return hashlib.sha256(address.encode()).hexdigest()
    return hash_phone(address)
