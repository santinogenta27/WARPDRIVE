"""Tarea 2 — test de aceptación: un tenant no puede leer filas de otro."""

import uuid

import psycopg
import pytest
from psycopg import errors

from app.db.connection import tenant_scope
from app.db.migrate import migrate

# Tablas de negocio: todas llevan tenant_id y RLS. `tenants` se aísla por su propio id.
BUSINESS_TABLES = [
    "users",
    "sources",
    "conversations",
    "messages",
    "case_types",
    "documents",
    "chunks",
    "tools",
    "eval_cases",
    "eval_runs",
    "contacts",
    "cases",
    "approvals",
    "audit_events",
    "usage",
]


def _new_tenant(conn: psycopg.Connection, name: str) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values (%s) returning id", (name,)).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def _seed_two_tenants(conn: psycopg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """Crea dos tenants con un tipo de caso y un contacto cada uno (como dueño de la base)."""
    a = _new_tenant(conn, "Tenant A")
    b = _new_tenant(conn, "Tenant B")
    for tenant, label in ((a, "a"), (b, "b")):
        conn.execute(
            "insert into case_types (tenant_id, name) values (%s, %s)", (tenant, f"tipo_{label}")
        )
        conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s)",
            (tenant, f"hash_{label}"),
        )
    return a, b


def test_un_tenant_no_lee_filas_de_otro(conn: psycopg.Connection) -> None:
    a, b = _seed_two_tenants(conn)

    with tenant_scope(conn, a):
        names = [r[0] for r in conn.execute("select name from case_types")]
        tenants = [r[0] for r in conn.execute("select id from tenants")]
        contacts = [r[0] for r in conn.execute("select phone_hash from contacts")]
    assert names == ["tipo_a"]
    assert tenants == [a]
    assert contacts == ["hash_a"]

    with tenant_scope(conn, b):
        names = [r[0] for r in conn.execute("select name from case_types")]
    assert names == ["tipo_b"]


def test_sin_tenant_definido_no_ve_nada(conn: psycopg.Connection) -> None:
    _seed_two_tenants(conn)
    conn.execute("set local role app_user")
    assert conn.execute("select count(*) from case_types").fetchone() == (0,)
    assert conn.execute("select count(*) from tenants").fetchone() == (0,)


def test_no_puede_insertar_filas_para_otro_tenant(conn: psycopg.Connection) -> None:
    a, b = _seed_two_tenants(conn)
    with tenant_scope(conn, a), pytest.raises(errors.InsufficientPrivilege):
        conn.execute("insert into case_types (tenant_id, name) values (%s, 'intruso')", (b,))


def test_no_puede_referenciar_filas_de_otro_tenant(conn: psycopg.Connection) -> None:
    """Una clave foránea no puede apuntar a una fila de otro tenant, aunque se conozca su id."""
    a, b = _seed_two_tenants(conn)
    row = conn.execute("select id from contacts where tenant_id = %s", (b,)).fetchone()
    assert row is not None
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute("insert into cases (tenant_id, contact_id) values (%s, %s)", (a, row[0]))


def test_toda_tabla_de_negocio_tiene_tenant_id_y_rls(conn: psycopg.Connection) -> None:
    for table in BUSINESS_TABLES + ["tenants"]:
        row = conn.execute(
            "select relrowsecurity, relforcerowsecurity from pg_class "
            "where relname = %s and relnamespace = 'public'::regnamespace",
            (table,),
        ).fetchone()
        assert row == (True, True), f"{table} sin RLS forzada"
        policies = conn.execute(
            "select count(*) from pg_policies where schemaname = 'public' and tablename = %s",
            (table,),
        ).fetchone()
        assert policies is not None and policies[0] >= 1, f"{table} sin política"
    for table in BUSINESS_TABLES:
        has_column = conn.execute(
            "select 1 from information_schema.columns "
            "where table_schema = 'public' and table_name = %s and column_name = 'tenant_id' "
            "and is_nullable = 'NO'",
            (table,),
        ).fetchone()
        assert has_column, f"{table} sin tenant_id obligatorio"


def test_audit_events_es_solo_insercion(conn: psycopg.Connection) -> None:
    a, _ = _seed_two_tenants(conn)
    with tenant_scope(conn, a):
        conn.execute(
            "insert into audit_events (tenant_id, kind, payload, hash) "
            "values (%s, 'prueba', '{}', 'h1')",
            (a,),
        )
        conn.execute("savepoint antes_de_update")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("update audit_events set kind = 'alterado'")
        conn.execute("rollback to savepoint antes_de_update")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("delete from audit_events")


def test_alcances_anidados_no_pierden_la_rls(conn: psycopg.Connection) -> None:
    a, b = _seed_two_tenants(conn)
    with tenant_scope(conn, a):
        with tenant_scope(conn, a):
            pass
        names = [r[0] for r in conn.execute("select name from case_types")]
        assert names == ["tipo_a"]  # el bloque interno no devolvió el rol dueño
        with pytest.raises(RuntimeError), tenant_scope(conn, b):
            pass


def test_las_migraciones_son_idempotentes(migrated: str) -> None:
    assert migrate(migrated) == []
