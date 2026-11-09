"""Cifrado simétrico de datos sensibles en la base (Fernet con ENCRYPTION_KEY)."""

import os

from cryptography.fernet import Fernet

from app.config import load_dotenv


def _fernet() -> Fernet:
    load_dotenv()
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            'Falta ENCRYPTION_KEY en .env. Generala con: python -c "from cryptography.fernet'
            ' import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
