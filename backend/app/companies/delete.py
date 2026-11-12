"""Borrado de una empresa (decisión 034): todos sus datos, en una sola transacción.

Las tablas no están escritas acá: se buscan en el catálogo de Postgres todas las que guardan el
id de la empresa (`tenant_id` o una clave foránea a `tenants`). Así una tabla agregada por una
migración futura también se borra, sin tocar este código.

Solo lo usa el panel del operador, con la conexión dueña (fuera de un tenant). Deja un registro
en `operator_audit` con cantidades, nunca con datos de la empresa.
"""

import shutil
import uuid
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.config import REPO_ROOT


class DeleteError(ValueError):
    """El borrado no corresponde: el mensaje se le muestra al operador."""


def company(conn: psycopg.Connection, tenant_id: uuid.UUID) -> tuple[str, bool] | None:
    """(nombre, ¿es la demo?) de la empresa, o None si no existe."""
    row = conn.execute("select name, settings from tenants where id = %s", (tenant_id,)).fetchone()
    if row is None:
        return None
    return str(row[0]), (row[1] or {}).get("demo") == "acme"


def _references(conn: psycopg.Connection) -> list[tuple[str, str]]:
    """(tabla, columna) de todo lo que guarda el id de una empresa, en un orden que respeta las
    claves foráneas: primero las tablas que apuntan a otras."""
    columns: dict[str, str] = dict(
        conn.execute(
            "select table_name, column_name from information_schema.columns c"
            " join information_schema.tables t using (table_schema, table_name)"
            " where c.table_schema = 'public' and c.column_name = 'tenant_id'"
            " and t.table_type = 'BASE TABLE'"
        ).fetchall()
    )
    # Tablas que apuntan a `tenants` con otra columna.
    for table, column in conn.execute(
        "select c.conrelid::regclass::text, a.attname from pg_constraint c"
        " join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]"
        " where c.contype = 'f' and c.confrelid = 'tenants'::regclass"
    ).fetchall():
        columns.setdefault(str(table), str(column))
    columns.pop("tenants", None)
    edges = conn.execute(
        "select distinct conrelid::regclass::text, confrelid::regclass::text from pg_constraint"
        " where contype = 'f' and conrelid <> confrelid"
    ).fetchall()
    pending = set(columns)
    ordered: list[str] = []
    while pending:
        # Se puede borrar una tabla cuando ninguna de las que quedan apunta a ella.
        free = sorted(
            t
            for t in pending
            if not any(parent == t and child in pending for child, parent in edges)
        )
        if not free:  # ciclo de claves foráneas: no hay ninguno hoy
            raise DeleteError("No se pudo ordenar el borrado de las tablas.")
        ordered += free
        pending -= set(free)
    return [(table, columns[table]) for table in ordered]


def counts(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, int]:
    """Filas de la empresa por tabla (solo las que tienen alguna), para la confirmación."""
    found: dict[str, int] = {}
    for table, column in _references(conn):
        row = conn.execute(
            sql.SQL("select count(*) from {} where {} = %s").format(
                sql.Identifier(table), sql.Identifier(column)
            ),
            (tenant_id,),
        ).fetchone()
        if row and row[0]:
            found[table] = int(row[0])
    return found


def record(
    conn: psycopg.Connection,
    operator: str,
    action: str,
    tenant_id: uuid.UUID,
    name: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Anota una acción del operador de la plataforma (solo inserción)."""
    conn.execute(
        # La hora real, no la del comienzo de la transacción: el registro queda en orden.
        "insert into operator_audit (created_at, operator, action, company_id, company_name,"
        " detail) values (clock_timestamp(), %s, %s, %s, %s, %s)",
        (operator, action, tenant_id, name, Jsonb(detail or {})),
    )


def recent(conn: psycopg.Connection, limit: int = 20) -> list[tuple[Any, ...]]:
    return conn.execute(
        "select created_at, operator, action, company_name, detail from operator_audit"
        " order by created_at desc, id desc limit %s",
        (limit,),
    ).fetchall()


def delete_company(
    conn: psycopg.Connection, tenant_id: uuid.UUID, *, operator: str, confirm_name: str
) -> dict[str, int]:
    """Borra la empresa y todo lo suyo. `confirm_name`: el nombre, escrito por el operador para
    confirmar. Devuelve las filas borradas por tabla."""
    found = company(conn, tenant_id)
    if found is None:
        raise DeleteError("Esa empresa no existe.")
    name, demo = found
    if demo:
        raise DeleteError("La empresa de demo no se borra: es la que usa el panel del operador.")
    if confirm_name.strip() != name:
        raise DeleteError("El nombre no coincide. Escribilo tal cual figura para confirmar.")
    # Permite borrar la auditoría de casos de esta empresa, y solo de esta (migración 0019).
    conn.execute("select set_config('app.deleting_tenant', %s, true)", (str(tenant_id),))
    deleted: dict[str, int] = {}
    for table, column in _references(conn):
        cursor = conn.execute(
            sql.SQL("delete from {} where {} = %s").format(
                sql.Identifier(table), sql.Identifier(column)
            ),
            (tenant_id,),
        )
        if cursor.rowcount:
            deleted[table] = cursor.rowcount
    # La cola de jobs no es una tabla de negocio: la empresa va en el contenido del job.
    jobs = conn.execute(
        "delete from jobs where payload ->> 'tenant_id' = %s", (str(tenant_id),)
    ).rowcount
    if jobs:
        deleted["jobs"] = jobs
    deleted["tenants"] = conn.execute("delete from tenants where id = %s", (tenant_id,)).rowcount
    conn.execute("select set_config('app.deleting_tenant', '', true)")
    record(conn, operator, "company_deleted", tenant_id, name, {"filas": deleted})
    # Respuestas del modelo guardadas en disco durante su onboarding.
    shutil.rmtree(
        REPO_ROOT / "evals" / ".cache" / "onboarding-panel" / str(tenant_id), ignore_errors=True
    )
    return deleted
