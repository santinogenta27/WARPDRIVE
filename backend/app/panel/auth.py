"""Login simple del panel para la demo (decisión 022).

Un usuario y una clave de entorno (PANEL_EMAIL y PANEL_PASSWORD). La sesión es una cookie
firmada con HMAC (PANEL_SECRET o, si no está, ENCRYPTION_KEY) que vence a las 12 horas, y cada
formulario lleva un token anti-CSRF derivado de la sesión. La spec pide magic link por email y
roles por empresa: eso llega con el panel completo (Hito 5 de la spec).
"""

import base64
import hashlib
import hmac
import os
import time

from app.config import REPO_ROOT, load_dotenv

COOKIE = "panel"
SESSION_SECONDS = 12 * 3600
ENV_FILE = REPO_ROOT / ".env"


def _env(name: str) -> str:
    """Variable del entorno o, en local, del .env (sin pisar lo que ya está definido)."""
    load_dotenv(ENV_FILE)
    return os.environ.get(name, "")


def _secret() -> bytes:
    secret = _env("PANEL_SECRET") or _env("ENCRYPTION_KEY")
    if not secret:
        raise RuntimeError("Falta PANEL_SECRET o ENCRYPTION_KEY para firmar la sesión del panel")
    return secret.encode()


def _sign(data: str) -> str:
    return hmac.new(_secret(), data.encode(), hashlib.sha256).hexdigest()


def configured() -> bool:
    return bool(_env("PANEL_EMAIL") and _env("PANEL_PASSWORD"))


def check_credentials(email: str, password: str) -> bool:
    expected_email = _env("PANEL_EMAIL").strip().lower()
    expected_password = _env("PANEL_PASSWORD")
    if not expected_email or not expected_password:
        return False
    same_email = hmac.compare_digest(email.strip().lower(), expected_email)
    same_password = hmac.compare_digest(password, expected_password)
    return same_email and same_password


def new_session(email: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_SECONDS)
    payload = base64.urlsafe_b64encode(f"{email}|{expires}".encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(token: str | None, now: float | None = None) -> str | None:
    """El email de la sesión, o None si falta, está adulterada o venció."""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        email, expires = base64.urlsafe_b64decode(payload).decode().rsplit("|", 1)
    except ValueError:
        return None
    return email if int(expires) > (now or time.time()) else None


def csrf_token(session: str) -> str:
    return _sign(f"csrf:{session}")[:32]


def valid_csrf(session: str, token: str) -> bool:
    return hmac.compare_digest(csrf_token(session), token or "")
