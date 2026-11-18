"""Cifrado simétrico de datos sensibles en la base (Fernet con ENCRYPTION_KEY).

`encrypt`/`decrypt` usan la clave del sistema (tokens de canales). `encrypt_for`/`decrypt_for`
usan una clave derivada por empresa (HKDF con el id del tenant): lo cifrado para una empresa no
se puede descifrar con la clave de otra, aunque alguien copie la fila de una a otra.

Rotación (decisión 055): `ENCRYPTION_KEY` puede traer varias claves separadas por comas, la
nueva primero. Se cifra con la primera y se descifra con cualquiera; `python -m
app.rotate_keys` vuelve a cifrar todo con la primera, y después se saca la vieja.
"""

import base64
import os
import uuid

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import load_dotenv


def _masters() -> list[str]:
    load_dotenv()
    keys = [k.strip() for k in os.environ.get("ENCRYPTION_KEY", "").split(",") if k.strip()]
    if not keys:
        raise RuntimeError(
            'Falta ENCRYPTION_KEY en .env. Generala con: python -c "from cryptography.fernet'
            ' import Fernet; print(Fernet.generate_key().decode())"'
        )
    return keys


def _master() -> str:
    return _masters()[0]


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(key.encode()) for key in _masters()])


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def _derived(master: str, tenant_id: uuid.UUID) -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"tenant-credentials:{tenant_id}".encode(),
    ).derive(master.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def _tenant_fernet(tenant_id: uuid.UUID) -> MultiFernet:
    return MultiFernet([_derived(master, tenant_id) for master in _masters()])


def rotate(token: str) -> str:
    """Lo cifrado con la clave del sistema, vuelto a cifrar con la clave nueva."""
    return _fernet().rotate(token.encode()).decode()


def rotate_for(tenant_id: uuid.UUID, token: str) -> str:
    return _tenant_fernet(tenant_id).rotate(token.encode()).decode()


def encrypt_for(tenant_id: uuid.UUID, text: str) -> str:
    return _tenant_fernet(tenant_id).encrypt(text.encode()).decode()


def decrypt_for(tenant_id: uuid.UUID, token: str) -> str:
    """Lanza `cryptography.fernet.InvalidToken` si el dato se cifró para otra empresa."""
    return _tenant_fernet(tenant_id).decrypt(token.encode()).decode()
