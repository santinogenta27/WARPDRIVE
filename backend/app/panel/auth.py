"""Login del panel (decisiones 022 y 026).

Dos formas de entrar:

- **Operador de la plataforma:** un usuario y una clave de entorno (PANEL_EMAIL y
  PANEL_PASSWORD). Entra a la empresa de demo (Acme) y es el único que puede crear empresas.
- **Usuario de una empresa:** email y clave propia, guardada como hash scrypt en `users`. Entra
  solo a su empresa.

La sesión es una cookie firmada con HMAC (PANEL_SECRET o, si no está, ENCRYPTION_KEY) que vence a
las 12 horas y lleva la empresa del usuario: toda pantalla y todo endpoint del panel leen la
empresa de la sesión, nunca de la URL ni del formulario. Cada formulario lleva un token anti-CSRF
derivado de la sesión. La spec pide magic link por email: queda pendiente (decisión 029).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass

import psycopg

from app.config import REPO_ROOT, load_dotenv

COOKIE = "panel"
SESSION_SECONDS = 12 * 3600
ENV_FILE = REPO_ROOT / ".env"
MIN_PASSWORD = 10
# Intentos fallidos de login por email: frena la fuerza bruta (en memoria, un solo proceso).
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
_failures: dict[str, list[float]] = {}


@dataclass(frozen=True)
class Session:
    token: str
    email: str
    tenant_id: uuid.UUID | None  # None: operador sin empresa de demo cargada
    company: str
    admin: bool  # operador de la plataforma (login de entorno)
    # Rol en la empresa (decisión 058): `owner` (admin) o `agent` (persona del equipo). Las
    # sesiones de antes no lo traen y valen como admin.
    role: str = "owner"
    # Preferencias del usuario (decisión 085): idioma del panel y tema.
    language: str = "en"
    theme: str = "light"


# El panel sale en inglés salvo que el usuario elija otro idioma (decisión 085).
DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "es")
THEMES = ("light", "dark")


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


def operator_email() -> str:
    return _env("PANEL_EMAIL").strip().lower()


def check_credentials(email: str, password: str) -> bool:
    """Login del operador de la plataforma (variables de entorno)."""
    expected_email = _env("PANEL_EMAIL").strip().lower()
    expected_password = _env("PANEL_PASSWORD")
    if not expected_email or not expected_password:
        return False
    same_email = hmac.compare_digest(email.strip().lower().encode(), expected_email.encode())
    same_password = hmac.compare_digest(password.encode(), expected_password.encode())
    return same_email and same_password


# -- claves de los usuarios de cada empresa ---------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1, dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password("clave-que-no-es-de-nadie")


@dataclass(frozen=True)
class CompanyUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    company: str
    email: str
    role: str = "owner"
    preferences: dict[str, str] | None = None


def company_login(conn: psycopg.Connection, email: str, password: str) -> CompanyUser | None:
    """Usuario de una empresa por email y clave. Es una consulta fuera de un tenant, como el
    ruteo de un webhook: todavía no se sabe de qué empresa es. El índice único garantiza que un
    email con clave pertenece a una sola empresa."""
    row = conn.execute(
        "select u.id, u.tenant_id, t.name, u.email, u.password_hash, u.role, u.preferences"
        " from users u"
        " join tenants t on t.id = u.tenant_id"
        " where lower(u.email) = %s and u.password_hash is not null and t.status = 'active'",
        (email.strip().lower(),),
    ).fetchone()
    # Sin usuario se verifica igual contra un hash cualquiera: el tiempo no revela si existe.
    if not verify_password(password, row[4] if row else _DUMMY_HASH) or row is None:
        return None
    return CompanyUser(
        user_id=row[0],
        tenant_id=row[1],
        company=row[2],
        email=row[3],
        role=str(row[5]),
        preferences=dict(row[6] or {}),
    )


def save_preferences(
    conn: psycopg.Connection, tenant_id: uuid.UUID, email: str, changes: dict[str, str]
) -> None:
    """Guarda el idioma o el tema del usuario de una empresa (decisión 085)."""
    from psycopg.types.json import Jsonb

    from app.db.connection import tenant_scope

    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update users set preferences = preferences || %s where lower(email) = %s",
            (Jsonb(changes), email.strip().lower()),
        )


def language_or_default(value: object) -> str:
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def too_many_failures(email: str, now: float | None = None) -> bool:
    moment = now or time.time()
    recent = [t for t in _failures.get(email.strip().lower(), []) if t > moment - FAILURE_WINDOW]
    return len(recent) >= MAX_FAILURES


def record_failure(email: str, now: float | None = None) -> None:
    moment = now or time.time()
    key = email.strip().lower()
    _failures[key] = [t for t in _failures.get(key, []) if t > moment - FAILURE_WINDOW] + [moment]
    if len(_failures) > 10_000:  # no crece sin límite
        _failures.clear()


def clear_failures(email: str) -> None:
    _failures.pop(email.strip().lower(), None)


# -- sesión -----------------------------------------------------------------------------------
def new_session(
    email: str,
    now: float | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    company: str = "",
    admin: bool = True,
    role: str = "owner",
    language: str | None = None,
    theme: str = "light",
) -> str:
    """Cookie de sesión. Sin `tenant_id` es la del operador: entra a la empresa de demo."""
    claims = {
        "e": email,
        "t": str(tenant_id) if tenant_id else None,
        "c": company,
        "a": admin,
        "r": role,
        "l": language_or_default(language),
        "th": theme if theme in THEMES else "light",
        "x": int((now or time.time()) + SESSION_SECONDS),
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_claims(token: str | None, now: float | None = None) -> Session | None:
    """La sesión, o None si falta, está adulterada o venció."""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature.encode(), _sign(payload).encode()):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        if int(claims["x"]) <= (now or time.time()):
            return None
        tenant = uuid.UUID(claims["t"]) if claims.get("t") else None
        return Session(
            token,
            str(claims["e"]),
            tenant,
            str(claims.get("c") or ""),
            bool(claims["a"]),
            str(claims.get("r") or "owner"),
            language_or_default(claims.get("l")),
            "dark" if claims.get("th") == "dark" else "light",
        )
    except (ValueError, KeyError, TypeError):
        return None


def read_session(token: str | None, now: float | None = None) -> str | None:
    """El email de la sesión, o None si falta, está adulterada o venció."""
    session = read_claims(token, now)
    return session.email if session else None


def csrf_token(session: str) -> str:
    return _sign(f"csrf:{session}")[:32]


def valid_csrf(session: str, token: str) -> bool:
    return hmac.compare_digest(csrf_token(session).encode(), (token or "").encode())
