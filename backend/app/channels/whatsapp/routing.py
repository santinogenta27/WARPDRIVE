"""Qué empresa atiende cada número de WhatsApp (tabla `channels`) y con qué token responde."""

import uuid
from dataclasses import dataclass

import psycopg

from app.crypto import decrypt, encrypt
from app.db.connection import tenant_scope


@dataclass(frozen=True)
class Channel:
    id: uuid.UUID
    tenant_id: uuid.UUID
    phone_number_id: str
    token: str | None  # None: el canal no tiene token guardado (se usa el del entorno)


def _channel(row: tuple[uuid.UUID, uuid.UUID, str, str | None]) -> Channel:
    token = decrypt(row[3]) if row[3] else None
    return Channel(id=row[0], tenant_id=row[1], phone_number_id=row[2], token=token)


def channel_for_number(conn: psycopg.Connection, phone_number_id: str) -> Channel | None:
    """Canal activo del número. Es la única consulta fuera de un tenant: el ruteo del webhook,
    por un número que pertenece a una sola empresa (índice único)."""
    row = conn.execute(
        "select id, tenant_id, phone_number_id, access_token_enc from channels"
        " where phone_number_id = %s and kind = 'whatsapp' and status = 'active'",
        (phone_number_id,),
    ).fetchone()
    return _channel(row) if row else None


def channel_for_tenant(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Channel | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select id, tenant_id, phone_number_id, access_token_enc from channels"
            " where kind = 'whatsapp' and status = 'active' order by created_at limit 1"
        ).fetchone()
    return _channel(row) if row else None


def register_whatsapp_channel(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    phone_number_id: str,
    token: str,
    waba_id: str | None = None,
) -> uuid.UUID:
    """Asigna (o actualiza) el número de WhatsApp de una empresa, con el token cifrado."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into channels (tenant_id, kind, phone_number_id, waba_id, access_token_enc)"
            " values (%s, 'whatsapp', %s, %s, %s)"
            " on conflict (phone_number_id) where phone_number_id is not null do update set"
            " waba_id = coalesce(excluded.waba_id, channels.waba_id),"
            " access_token_enc = excluded.access_token_enc, status = 'active'"
            " returning id",
            (tenant_id, phone_number_id, waba_id, encrypt(token)),
        ).fetchone()
    assert row is not None
    channel_id: uuid.UUID = row[0]
    return channel_id
