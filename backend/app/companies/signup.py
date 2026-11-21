"""Registro self-service e invitaciones con roles (decisión 058).

* **Registro:** la empresa deja su nombre, email y clave en `/registro`. Se le envía un enlace;
  al abrirlo, se crea la empresa con ese usuario como admin (`owner`). Sin confirmar el email no
  se crea nada. El enlace vence a las 24 horas y sirve una vez.
* **Invitaciones:** un admin invita a un compañero con su rol: admin u persona del equipo
  (`agent`, que atiende Casos y Aprobaciones pero no toca la configuración). La invitación vence
  a los 7 días; al aceptarla, el invitado elige su clave.
* Solo se guardan hashes del enlace y de la clave. El email del enlace no dice si el email ya
  tenía cuenta: el mensaje en pantalla es siempre el mismo.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

from app.companies.create import EMAIL, Company, CompanyError, create_company
from app.db.connection import tenant_scope
from app.mailer import Email, Mailer
from app.panel import auth

SIGNUP_TTL = timedelta(hours=24)
INVITE_TTL = timedelta(days=7)
MAX_SIGNUPS_PER_DAY = 5
ROLES = {"owner": "Admin", "agent": "Persona del equipo"}


class SignupError(ValueError):
    """Dato inválido: el mensaje se le muestra a quien se registra o invita."""


@dataclass(frozen=True)
class Member:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    company: str
    email: str
    role: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def start_signup(
    conn: psycopg.Connection,
    mailer: Mailer,
    *,
    company: str,
    email: str,
    password: str,
    base_url: str,
    trial_token: str = "",
) -> None:
    """Guarda el registro pendiente y envía el enlace de confirmación. `trial_token`: viene de
    "Empezar mi piloto" en una prueba gratis; al confirmar, se convierte esa empresa de prueba
    (decisión 075). El email tiene que ser el de la prueba."""
    company, email = company.strip(), email.strip().lower()
    trial_id = None
    if trial_token:
        from app.trial import service

        trial = service.find(conn, trial_token)
        if trial is None or trial.status not in service.ACTIVE:
            raise SignupError("La prueba ya no está disponible. Registrate sin ella.")
        if email != trial.email:
            raise SignupError("Usá el email con el que hiciste la prueba.")
        trial_id = trial.id
        service.request_pilot(conn, trial.id)
    if not 2 <= len(company) <= 80:
        raise SignupError("El nombre de la empresa tiene que tener entre 2 y 80 caracteres.")
    if not EMAIL.match(email) or len(email) > 200:
        raise SignupError("El email no es válido.")
    if len(password) < auth.MIN_PASSWORD:
        raise SignupError(f"La clave tiene que tener al menos {auth.MIN_PASSWORD} caracteres.")
    conn.execute("delete from signups where expires_at < now()")
    row = conn.execute(
        "select count(*) from signups where lower(email) = %s and created_at > now() - interval"
        " '1 day'",
        (email,),
    ).fetchone()
    if row is not None and row[0] >= MAX_SIGNUPS_PER_DAY:
        raise SignupError("Demasiados registros con ese email. Probá de nuevo mañana.")
    token = secrets.token_urlsafe(32)
    conn.execute(
        "insert into signups (company, email, password_hash, token_hash, expires_at, trial_id)"
        " values (%s, %s, %s, %s, %s, %s)",
        (
            company,
            email,
            auth.hash_password(password),
            _hash(token),
            _now() + SIGNUP_TTL,
            trial_id,
        ),
    )
    link = f"{base_url.rstrip('/')}/registro/confirmar?token={token}"
    mailer.send(
        Email(
            to=email,
            subject=f"Confirmá tu email para crear {company}",
            body=(
                f"Para crear la cuenta de {company}, abrí este enlace (vence en 24 horas):\n\n"
                f"{link}\n\nSi no te registraste, ignorá este email."
            ),
        )
    )


def confirm_signup(conn: psycopg.Connection, token: str) -> Company:
    """Crea la empresa del registro. El enlace sirve una sola vez."""
    row = conn.execute(
        "delete from signups where token_hash = %s and expires_at > now()"
        " returning company, email, password_hash, trial_id",
        (_hash(token),),
    ).fetchone()
    if row is None:
        raise SignupError("El enlace no es válido o venció. Registrate de nuevo.")
    company, email, password_hash, trial_id = row
    try:
        if trial_id is not None:  # "Empezar mi piloto": la empresa de prueba pasa a ser real
            from app.trial import service

            return service.convert(
                conn, trial_id, company=company, email=email, password_hash=password_hash
            )
        return create_company(conn, company, email, "", password_hash=password_hash)
    except CompanyError as error:
        raise SignupError(str(error)) from error


def invite(
    conn: psycopg.Connection,
    mailer: Mailer,
    tenant_id: uuid.UUID,
    *,
    company: str,
    email: str,
    role: str,
    base_url: str,
) -> None:
    email = email.strip().lower()
    if role not in ROLES:
        raise SignupError("Elegí un rol de la lista.")
    if not EMAIL.match(email):
        raise SignupError("El email no es válido.")
    used = conn.execute(
        "select 1 from users where lower(email) = %s and password_hash is not null", (email,)
    ).fetchone()
    if used or email == auth.operator_email():
        raise SignupError("Ese email ya tiene un usuario.")
    token = secrets.token_urlsafe(32)
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into invitations (tenant_id, email, role, token_hash, expires_at)"
            " values (%s, %s, %s, %s, %s)",
            (tenant_id, email, role, _hash(token), _now() + INVITE_TTL),
        )
    link = f"{base_url.rstrip('/')}/panel/invitacion?token={token}"
    mailer.send(
        Email(
            to=email,
            subject=f"Te invitaron al panel de {company}",
            body=(
                f"Te invitaron a atender a los clientes de {company} como "
                f"{ROLES[role].lower()}. Para entrar, elegí tu clave en este enlace (vence en 7 "
                f"días):\n\n{link}"
            ),
        )
    )


def accept_invitation(conn: psycopg.Connection, token: str, password: str) -> Member:
    """El invitado elige su clave y queda como usuario de la empresa con su rol."""
    if len(password) < auth.MIN_PASSWORD:
        raise SignupError(f"La clave tiene que tener al menos {auth.MIN_PASSWORD} caracteres.")
    # Fuera de un tenant, como el login: todavía no se sabe de qué empresa es.
    row = conn.execute(
        "update invitations set accepted_at = now() where token_hash = %s"
        " and accepted_at is null and expires_at > now()"
        " returning tenant_id, email, role",
        (_hash(token),),
    ).fetchone()
    if row is None:
        raise SignupError("La invitación no es válida, ya se usó o venció.")
    tenant_id, email, role = row
    name = conn.execute("select name from tenants where id = %s", (tenant_id,)).fetchone()
    assert name is not None
    with tenant_scope(conn, tenant_id):
        user = conn.execute(
            "insert into users (tenant_id, email, role, password_hash) values (%s, %s, %s, %s)"
            " on conflict (tenant_id, email) do update set role = excluded.role,"
            " password_hash = excluded.password_hash returning id",
            (tenant_id, email, role, auth.hash_password(password)),
        ).fetchone()
    assert user is not None
    return Member(user[0], tenant_id, str(name[0]), email, role)


def members(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[str, str, bool]]:
    """(email, rol, ¿pendiente?) de los usuarios y las invitaciones vigentes."""
    with tenant_scope(conn, tenant_id):
        users = conn.execute(
            "select email, role from users where password_hash is not null order by created_at"
        ).fetchall()
        pending = conn.execute(
            "select email, role from invitations where accepted_at is null and expires_at > now()"
            " order by created_at"
        ).fetchall()
    return [(str(e), str(r), False) for e, r in users] + [
        (str(e), str(r), True) for e, r in pending
    ]
