"""Qué empresa atiende cada número de WhatsApp y con qué número le escribe a sus clientes."""

import uuid

import psycopg


def tenant_for_number(conn: psycopg.Connection, phone_number_id: str) -> uuid.UUID | None:
    """Empresa dueña del número. Única consulta fuera de un tenant: es el ruteo del webhook."""
    row = conn.execute(
        "select id from tenants where settings -> 'whatsapp' ->> 'phone_number_id' = %s",
        (phone_number_id,),
    ).fetchone()
    return row[0] if row else None


def number_for_tenant(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str | None:
    row = conn.execute(
        "select settings -> 'whatsapp' ->> 'phone_number_id' from tenants where id = %s",
        (tenant_id,),
    ).fetchone()
    return row[0] if row and row[0] else None
