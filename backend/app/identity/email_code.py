"""Código de 6 dígitos al email registrado: vence en 10 minutos, con hasta 3 intentos (spec).

Estados de una verificación: `awaiting_email` (el agente pidió el email) → `code_sent` →
`verified` o `failed`. Si el email no está registrado igual se responde como si se hubiera
enviado, para no revelar qué emails existen, y ningún código lo verifica.
"""

import hashlib
import hmac
import html
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Literal

import psycopg
from cryptography.fernet import InvalidToken

from app import crypto, i18n
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.mailer import Email

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 3
VERIFIED_DAYS = 30
logger = logging.getLogger(__name__)

CheckResult = Literal["verified", "wrong", "locked", "expired"]


@dataclass(frozen=True)
class Verification:
    id: uuid.UUID
    case_id: uuid.UUID
    question: str
    status: str


def code_email(*, to: str, company: str, code: str, language: str) -> Email:
    """El email con el código: asunto, texto plano y HTML con el código grande y la vigencia
    (decisión 043). HTML con estilos en línea y tablas: así lo muestran Gmail y Outlook."""

    def say(key: str) -> str:
        return i18n.text(language, key, empresa=company, code=code, minutes=CODE_TTL_MINUTES)

    body = f"{say('email_body')}\n\n{say('email_ignore')}"
    safe = {key: html.escape(say(key)) for key in ("email_intro", "email_expires", "email_ignore")}
    font = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    page = (
        f'<!doctype html><html lang="{i18n.normalize(language)}"><body style="margin:0;'
        f'padding:0;background:#f4f4f5">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        ' style="background:#f4f4f5;padding:32px 16px"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        ' style="max-width:460px;background:#ffffff;border-radius:16px;'
        f'border:1px solid #e4e4e7;font-family:{font};color:#18181b">'
        '<tr><td style="padding:28px 32px 0;font-size:15px;font-weight:600">'
        f"{html.escape(company)}</td></tr>"
        '<tr><td style="padding:12px 32px 0;font-size:15px;line-height:1.5;color:#3f3f46">'
        f"{safe['email_intro']}</td></tr>"
        '<tr><td style="padding:20px 32px 0"><table role="presentation" width="100%"'
        ' cellpadding="0" cellspacing="0"><tr>'
        '<td align="center" style="font-family:ui-monospace,Menlo,Consolas,monospace;'
        "font-size:34px;font-weight:700;letter-spacing:8px;color:#18181b;background:#f4f4f5;"
        f'border-radius:12px;padding:18px 0">{html.escape(code)}</td>'
        "</tr></table></td></tr>"
        '<tr><td style="padding:16px 32px 0;font-size:14px;font-weight:600;color:#18181b">'
        f"{safe['email_expires']}</td></tr>"
        '<tr><td style="padding:12px 32px 28px;font-size:13px;line-height:1.5;color:#71717a">'
        f"{safe['email_ignore']}</td></tr>"
        "</table></td></tr></table></body></html>"
    )
    return Email(to=to, subject=say("email_subject"), body=body, html=page)


def _hash(verification_id: uuid.UUID, code: str) -> str:
    return hashlib.sha256(f"{verification_id}:{code}".encode()).hexdigest()


def open_verification(
    conn: psycopg.Connection, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> Verification | None:
    """La verificación en curso del contacto (de las últimas 24 h), si hay una."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select id, case_id, question, status from identity_verifications"
            " where contact_id = %s and status in ('awaiting_email', 'code_sent')"
            " and created_at > now() - interval '1 day'"
            " order by created_at desc limit 1",
            (contact_id,),
        ).fetchone()
    return Verification(*row) if row else None


def request_email(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    case_id: uuid.UUID,
    question: str,
) -> None:
    """El agente pidió el email: abre una verificación nueva (y cierra la anterior)."""
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update identity_verifications set status = 'failed'"
            " where contact_id = %s and status in ('awaiting_email', 'code_sent')",
            (contact_id,),
        )
        conn.execute(
            "insert into identity_verifications (tenant_id, contact_id, case_id, question)"
            " values (%s, %s, %s, %s)",
            (tenant_id, contact_id, case_id, question),
        )


def send_code(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    verification: Verification,
    customer_phone: str | None,
    email: str | None = None,
) -> str:
    """Genera un código nuevo para el email dado. `customer_phone`: el cliente que tiene ese
    email en el sistema de la empresa (None si no está registrado: ningún código verifica).
    `email`: se guarda cifrado si es de un cliente, para confirmarle después un reembolso
    (decisión 046)."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    sealed = _seal(tenant_id, email) if customer_phone and email else None
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update identity_verifications set status = 'code_sent', code_hash = %s,"
            " phone_hash = %s, email_enc = %s, attempts = 0,"
            " expires_at = now() + make_interval(mins => %s) where id = %s",
            (
                _hash(verification.id, code),
                hash_phone(customer_phone) if customer_phone else None,
                sealed,
                CODE_TTL_MINUTES,
                verification.id,
            ),
        )
    return code


def _seal(tenant_id: uuid.UUID, email: str) -> str | None:
    """El email cifrado con la clave de la empresa. Sin ENCRYPTION_KEY no se guarda: la
    verificación sigue y solo falta el email de confirmación."""
    try:
        return crypto.encrypt_for(tenant_id, email.strip())
    except RuntimeError:
        logger.warning("Sin ENCRYPTION_KEY: no se guarda el email de la verificación.")
        return None


def verified_email(
    conn: psycopg.Connection, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> str | None:
    """El email con el que el contacto se verificó, mientras la verificación sigue vigente y es
    la del cliente que el contacto demostró ser. None si no hay uno."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select v.email_enc from identity_verifications v"
            " join contacts c on c.id = v.contact_id"
            " where v.contact_id = %s and v.status = 'verified' and v.email_enc is not null"
            " and v.phone_hash = c.verified_phone_hash and c.verified_until > now()"
            " order by v.created_at desc limit 1",
            (contact_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        return crypto.decrypt_for(tenant_id, row[0])
    except (RuntimeError, InvalidToken):
        return None


def check_code(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    verification: Verification,
    code: str,
) -> tuple[CheckResult, int]:
    """Resultado e intentos restantes. Al verificar, el contacto queda verificado 30 días como
    el cliente del email."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select code_hash, phone_hash, attempts, expires_at < now()"
            " from identity_verifications where id = %s for update",
            (verification.id,),
        ).fetchone()
        assert row is not None
        code_hash, phone_hash, attempts, expired = row
        if expired:
            _close(conn, verification.id, "failed")
            return "expired", 0
        if phone_hash and hmac.compare_digest(code_hash, _hash(verification.id, code)):
            _close(conn, verification.id, "verified")
            conn.execute(
                "update contacts set verified_phone_hash = %s,"
                " verified_until = now() + make_interval(days => %s) where id = %s",
                (phone_hash, VERIFIED_DAYS, contact_id),
            )
            return "verified", 0
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "update identity_verifications set attempts = %s, status = 'failed' where id = %s",
                (attempts, verification.id),
            )
            return "locked", 0
        conn.execute(
            "update identity_verifications set attempts = %s where id = %s",
            (attempts, verification.id),
        )
        return "wrong", MAX_ATTEMPTS - attempts


def _close(conn: psycopg.Connection, verification_id: uuid.UUID, status: str) -> None:
    conn.execute(
        "update identity_verifications set status = %s where id = %s", (status, verification_id)
    )
