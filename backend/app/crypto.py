"""Cifrado simétrico de datos sensibles en la base (Fernet con ENCRYPTION_KEY).

`encrypt`/`decrypt` usan la clave del sistema (tokens de canales). `encrypt_for`/`decrypt_for`
usan una clave derivada por empresa (HKDF con el id del tenant): lo cifrado para una empresa no
se puede descifrar con la clave de otra, aunque alguien copie la fila de una a otra.
"""

import base64
import os
import uuid

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import load_dotenv


def _master() -> str:
    load_dotenv()
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            'Falta ENCRYPTION_KEY en .env. Generala con: python -c "from cryptography.fernet'
            ' import Fernet; print(Fernet.generate_key().decode())"'
        )
    return key


def _fernet() -> Fernet:
    return Fernet(_master().encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def _tenant_fernet(tenant_id: uuid.UUID) -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"tenant-credentials:{tenant_id}".encode(),
    ).derive(_master().encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_for(tenant_id: uuid.UUID, text: str) -> str:
    return _tenant_fernet(tenant_id).encrypt(text.encode()).decode()


def decrypt_for(tenant_id: uuid.UUID, token: str) -> str:
    """Lanza `cryptography.fernet.InvalidToken` si el dato se cifró para otra empresa."""
    return _tenant_fernet(tenant_id).decrypt(token.encode()).decode()
