"""Alta de una empresa desde el panel (decisión 026): el tenant, su primer usuario (dueño, con
clave propia) y su chat web de prueba.

La empresa nace vacía y aislada: sin tipos de caso, sin herramientas y sin documentos. Todo lo
que el agente sepa de ella sale de su propio onboarding.
"""

import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from app.channels.whatsapp.routing import register_whatsapp_channel
from app.contacts import WEB_PREFIX
from app.db.connection import tenant_scope
from app.panel import auth

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CompanyError(ValueError):
    """Dato inválido del alta: el mensaje se le muestra a quien la crea."""


@dataclass(frozen=True)
class Company:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    chat_slug: str  # el chat de prueba queda en /c/<chat_slug>


def slugify(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:30] or "empresa"


def chat_address(slug: str) -> str:
    return f"{WEB_PREFIX}{slug}"


def create_company(conn: psycopg.Connection, name: str, email: str, password: str) -> Company:
    name, email = name.strip(), email.strip().lower()
    if not 2 <= len(name) <= 80:
        raise CompanyError("El nombre de la empresa tiene que tener entre 2 y 80 caracteres.")
    if not EMAIL.match(email) or len(email) > 200:
        raise CompanyError("El email del usuario no es válido.")
    if len(password) < auth.MIN_PASSWORD:
        raise CompanyError(f"La clave tiene que tener al menos {auth.MIN_PASSWORD} caracteres.")
    # Consultas fuera de un tenant (como el login): el nombre y el email son únicos entre todas.
    taken = conn.execute(
        "select 1 from tenants where lower(name) = %s and status <> 'deleted'", (name.lower(),)
    ).fetchone()
    if taken:
        raise CompanyError("Ya hay una empresa con ese nombre.")
    used = conn.execute(
        "select 1 from users where lower(email) = %s and password_hash is not null", (email,)
    ).fetchone()
    if used or email == auth.operator_email():
        raise CompanyError("Ese email ya tiene un usuario.")
    row = conn.execute(
        "insert into tenants (name, settings) values (%s, %s) returning id",
        (name, Jsonb({"tone": "rioplatense"})),
    ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    with tenant_scope(conn, tenant_id):
        user = conn.execute(
            "insert into users (tenant_id, email, role, password_hash)"
            " values (%s, %s, 'owner', %s) returning id",
            (tenant_id, email, auth.hash_password(password)),
        ).fetchone()
    assert user is not None
    # El enlace del chat de prueba no se adivina: lleva una parte al azar.
    slug = f"{slugify(name)}-{secrets.token_hex(4)}"
    register_whatsapp_channel(conn, tenant_id, phone_number_id=chat_address(slug), kind="web")
    return Company(tenant_id=tenant_id, user_id=user[0], name=name, chat_slug=slug)


def chat_slug(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str | None:
    """El enlace del chat de prueba de la empresa (`/c/<slug>`), si tiene uno."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select phone_number_id from channels where kind = 'web' and status = 'active'"
            " order by created_at limit 1"
        ).fetchone()
    if row is None or not str(row[0]).startswith(WEB_PREFIX):
        return None
    return str(row[0])[len(WEB_PREFIX) :]
