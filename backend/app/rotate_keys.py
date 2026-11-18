"""Vuelve a cifrar todo con la clave nueva de ENCRYPTION_KEY (decisión 055).

Uso, con `ENCRYPTION_KEY=nueva,vieja` en el entorno:

    python -m app.rotate_keys

Recorre cada dato cifrado de la base y lo cifra con la primera clave, en una transacción. Es
idempotente: correrlo de nuevo no cambia nada que importe. Después se deja solo la clave nueva.
Corre con la conexión dueña de la base (fuera de un tenant), como el borrado de empresas.
"""

import json
import uuid
from collections.abc import Callable

import psycopg
from psycopg import sql

from app import crypto

# (tabla, columna) cifradas con la clave del sistema y con la clave de cada empresa.
SYSTEM = [("contacts", "wa_address_enc"), ("channels", "access_token_enc")]
# system_connections no tiene `id`: su clave es la empresa.
PER_TENANT = [("system_connections", "credentials_enc"), ("identity_verifications", "email_enc")]
KEYS = {"system_connections": "tenant_id"}


def _column(
    conn: psycopg.Connection,
    table: str,
    column: str,
    again: Callable[[uuid.UUID, str], str],
    only: list[uuid.UUID] | None,
) -> int:
    names = {
        "key": sql.Identifier(KEYS.get(table, "id")),
        "table": sql.Identifier(table),
        "column": sql.Identifier(column),
    }
    rows = conn.execute(
        sql.SQL(
            "select {key}, tenant_id, {column} from {table} where {column} is not null"
            " and (%s::uuid[] is null or tenant_id = any(%s::uuid[]))"
        ).format(**names),
        (only, only),
    ).fetchall()
    for row_id, tenant_id, token in rows:
        conn.execute(
            sql.SQL("update {table} set {column} = %s where {key} = %s").format(**names),
            (again(tenant_id, token), row_id),
        )
    return len(rows)


def rotate_all(conn: psycopg.Connection, only: list[uuid.UUID] | None = None) -> dict[str, int]:
    """`only`: solo esas empresas (para probarlo en una base compartida); None, todas."""
    done: dict[str, int] = {}
    for table, column in SYSTEM:
        done[f"{table}.{column}"] = _column(
            conn, table, column, lambda _, t: crypto.rotate(t), only
        )
    for table, column in PER_TENANT:
        done[f"{table}.{column}"] = _column(conn, table, column, crypto.rotate_for, only)
    keys = 0
    for tenant_id, llm in conn.execute(
        "select id, settings -> 'llm' from tenants where settings -> 'llm' ? 'key_enc'"
        " and (%s::uuid[] is null or id = any(%s::uuid[]))",
        (only, only),
    ).fetchall():
        llm["key_enc"] = crypto.rotate_for(tenant_id, llm["key_enc"])
        conn.execute(
            "update tenants set settings = jsonb_set(settings, '{llm}', %s::jsonb) where id = %s",
            (json.dumps(llm), tenant_id),
        )
        keys += 1
    done["tenants.settings.llm"] = keys
    return done


def main() -> None:
    from app.db.connection import connect

    with connect() as conn:
        with conn.transaction():
            done = rotate_all(conn)
    for name, count in done.items():
        print(f"{name}: {count}")
    print("Listo. Ya podés dejar solo la clave nueva en ENCRYPTION_KEY.")


if __name__ == "__main__":
    main()
