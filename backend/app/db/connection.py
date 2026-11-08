"""Conexiones a Postgres y alcance por tenant.

La aplicación opera con el rol `app_user`, que no tiene BYPASSRLS: toda consulta de negocio
pasa por `tenant_scope`, que fija el rol y el tenant solo para la transacción en curso
(`SET LOCAL`), compatible con el pooler de Neon.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from app.config import database_url


def connect(url: str | None = None) -> psycopg.Connection:
    """Abre una conexión transaccional. Sin prepared statements, por el pooler (PgBouncer)."""
    target = url or database_url()
    if not target:
        raise RuntimeError("DATABASE_URL no está definida")
    return psycopg.connect(target, prepare_threshold=None)


@contextmanager
def tenant_scope(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Iterator[psycopg.Connection]:
    """Ejecuta el bloque como `app_user` limitado a un tenant; restaura el rol al salir."""
    conn.execute("set local role app_user")
    conn.execute("select set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    try:
        yield conn
    finally:
        if not conn.closed and conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS:
            conn.execute("reset role")
            conn.execute("select set_config('app.tenant_id', '', true)")
