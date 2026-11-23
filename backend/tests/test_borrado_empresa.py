"""Hito 7, tarea 4 — test de aceptación: el operador borra una empresa y no queda ninguna fila
de ese tenant (decisión 034).

Desde el panel, con confirmación. Se borran todos sus datos, queda un registro en la auditoría
del operador y las demás empresas no se tocan.
"""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_empresas import (  # noqa: F401 — `world` es la fixture de test_empresas
    ADMIN,
    ADMIN_PASSWORD,
    NORTE,
    NORTE_PASSWORD,
    World,
    create_norte,
    csrf,
    login,
    post,
    upload_history,
    world,
)

from app.companies import delete
from app.companies.create import chat_slug, create_company
from app.db.connection import tenant_scope
from app.main import app


def tenant_tables(conn: psycopg.Connection) -> list[str]:
    """Todas las tablas con `tenant_id`, salgan de la migración que salgan."""
    rows = conn.execute(
        "select table_name from information_schema.columns where table_schema = 'public'"
        " and column_name = 'tenant_id' order by table_name"
    ).fetchall()
    return [r[0] for r in rows]


def rows_of(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, int]:
    """Filas del tenant en cada tabla, mirando como dueño de la base (sin RLS)."""
    found = {}
    for table in tenant_tables(conn):
        row = conn.execute(
            f'select count(*) from "{table}" where tenant_id = %s', (tenant_id,)
        ).fetchone()
        assert row is not None
        found[table] = int(row[0])
    return found


def fill_norte(world: World) -> tuple[TestClient, uuid.UUID]:  # noqa: F811
    """Tienda Norte con datos en muchas tablas: historial, documentos, casos, sesiones de chat,
    conexión con su sistema, corrida del onboarding y su job en la cola."""
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    assert "ok=" in upload_history(norte).headers["location"]
    post(norte, "/panel/onboarding/iniciar")
    post(
        norte,
        "/panel/configuracion/sistema/acceso",
        url="https://api.tiendanorte.example",
        auth="bearer",
        credencial="token-secreto",
    )
    chat = TestClient(app)
    slug = chat_slug(world.conn, tenant_id)
    session = chat.post("/chat/session", params={"canal": slug}).json()["session"]
    chat.post("/chat/messages", json={"session": session, "text": "¿Cuánto tarda el envío?"})
    return norte, tenant_id


def confirm_form(admin: TestClient, tenant_id: uuid.UUID) -> str:
    page = admin.get(f"/panel/empresas/{tenant_id}/borrar")
    assert page.status_code == 200
    return page.text


def test_borrar_una_empresa_no_deja_ninguna_fila_de_ese_tenant(world: World) -> None:  # noqa: F811
    conn = world.conn
    _, tenant_id = fill_norte(world)
    acme = world.stack.seed.tenant_id
    world.stack.send("lucia", "¿Abren los domingos?")  # Acme también tiene datos
    before = rows_of(conn, tenant_id)
    acme_before = rows_of(conn, acme)
    filled = [table for table, count in before.items() if count]
    assert len(filled) >= 14, filled  # el borrado se prueba con datos de verdad
    for table in ("audit_events", "cases", "chunks", "messages", "system_connections", "users"):
        assert before[table] > 0, table
    jobs = conn.execute(
        "select count(*) from jobs where payload ->> 'tenant_id' = %s", (str(tenant_id),)
    ).fetchone()
    assert jobs == (1,)

    admin = login(ADMIN, ADMIN_PASSWORD)
    listing = admin.get("/panel/operator/companies").text
    assert f"/panel/empresas/{tenant_id}/borrar" in listing
    form = confirm_form(admin, tenant_id)
    assert "Tienda Norte" in form and "no se puede deshacer" in form
    assert str(before["cases"]) in form  # muestra qué se va a borrar

    done = admin.post(
        f"/panel/empresas/{tenant_id}/borrar",
        data={"csrf": csrf(admin, "/panel/operator/companies"), "nombre": "Tienda Norte"},
        follow_redirects=False,
    )
    assert done.status_code == 303 and "ok=" in done.headers["location"]

    after = rows_of(conn, tenant_id)
    assert {table: count for table, count in after.items() if count} == {}
    assert conn.execute("select 1 from tenants where id = %s", (tenant_id,)).fetchone() is None
    jobs = conn.execute(
        "select count(*) from jobs where payload ->> 'tenant_id' = %s", (str(tenant_id),)
    ).fetchone()
    assert jobs == (0,)
    # Ninguna otra tabla guarda el id de la empresa, salvo el registro del operador.
    leftovers = conn.execute(
        "select c.table_name, c.column_name from information_schema.columns c"
        " where c.table_schema = 'public' and c.data_type = 'uuid'"
        " and c.table_name <> 'operator_audit'"
    ).fetchall()
    for table, column in leftovers:
        row = conn.execute(
            f'select count(*) from "{table}" where "{column}" = %s', (tenant_id,)
        ).fetchone()
        assert row == (0,), (table, column)

    # Acme queda exactamente como estaba.
    assert rows_of(conn, acme) == acme_before
    assert "Tienda Norte" not in admin.get("/panel/operator/companies").text.split("Registro")[0]


def test_el_borrado_queda_en_la_auditoria_del_operador(world: World) -> None:  # noqa: F811
    conn = world.conn
    _, tenant_id = fill_norte(world)
    cases = rows_of(conn, tenant_id)["cases"]
    admin = login(ADMIN, ADMIN_PASSWORD)
    admin.post(
        f"/panel/empresas/{tenant_id}/borrar",
        data={"csrf": csrf(admin, "/panel/operator/companies"), "nombre": "Tienda Norte"},
    )
    rows = conn.execute(
        "select operator, action, company_id, company_name, detail from operator_audit"
        " where company_id = %s order by created_at, id",
        (tenant_id,),
    ).fetchall()
    assert [r[1] for r in rows] == ["company_created", "company_deleted"]
    operator, _, company_id, name, detail = rows[-1]
    assert operator == ADMIN and company_id == tenant_id and name == "Tienda Norte"
    assert detail["filas"]["cases"] == cases and detail["filas"]["tenants"] == 1
    # Solo cantidades: ni mensajes, ni emails de clientes, ni credenciales.
    assert "token-secreto" not in str(detail) and "@" not in str(detail)
    page = admin.get("/panel/operator/companies").text
    assert "Registro del operador" in page and "Borró la empresa Tienda Norte" in page

    # El registro es de solo inserción, también para el dueño de la base.
    conn.execute("savepoint antes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from operator_audit where company_id = %s", (tenant_id,))
    conn.execute("rollback to savepoint antes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("update operator_audit set operator = 'otro'")
    conn.execute("rollback to savepoint antes")
    # Y la aplicación (app_user) ni siquiera lo puede leer.
    with tenant_scope(conn, world.stack.seed.tenant_id):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select 1 from operator_audit")


def test_el_borrado_pide_confirmacion_y_solo_lo_hace_el_operador(world: World) -> None:  # noqa: F811
    conn = world.conn
    norte, tenant_id = fill_norte(world)
    before = rows_of(conn, tenant_id)
    admin = login(ADMIN, ADMIN_PASSWORD)
    url = f"/panel/empresas/{tenant_id}/borrar"
    token = csrf(admin, "/panel/operator/companies")

    # Sin escribir el nombre exacto, no se borra.
    for name in ("", "tienda", "Acme Store"):
        wrong = admin.post(url, data={"csrf": token, "nombre": name}, follow_redirects=False)
        assert wrong.status_code == 303 and "error=" in wrong.headers["location"], name
    # Sin token anti-CSRF, tampoco.
    assert admin.post(url, data={"nombre": "Tienda Norte"}).status_code == 403
    # Un usuario de la empresa no puede borrarla, ni a otra, ni ver la pantalla.
    assert norte.get(url).status_code == 403
    own = norte.post(url, data={"csrf": csrf(norte), "nombre": "Tienda Norte"})
    assert own.status_code == 403
    acme_url = f"/panel/empresas/{world.stack.seed.tenant_id}/borrar"
    assert (
        norte.post(acme_url, data={"csrf": csrf(norte), "nombre": "Acme Store"}).status_code == 403
    )
    assert rows_of(conn, tenant_id) == before

    # La empresa de demo no se borra, ni una que no existe.
    conn.execute(
        'update tenants set settings = settings || \'{"demo": "acme"}\' where id = %s',
        (world.stack.seed.tenant_id,),
    )
    demo = admin.post(
        acme_url, data={"csrf": token, "nombre": "Acme Store"}, follow_redirects=False
    )
    assert "error=" in demo.headers["location"]
    assert rows_of(conn, world.stack.seed.tenant_id)["case_types"] > 0
    assert admin.get(f"/panel/empresas/{uuid.uuid4()}/borrar").status_code == 404
    with pytest.raises(delete.DeleteError):
        delete.delete_company(conn, uuid.uuid4(), operator=ADMIN, confirm_name="x")


def test_la_sesion_de_una_empresa_borrada_deja_de_servir(world: World) -> None:  # noqa: F811
    norte, tenant_id = fill_norte(world)
    slug = chat_slug(world.conn, tenant_id)
    assert norte.get("/panel/inbox").status_code == 200
    delete.delete_company(world.conn, tenant_id, operator=ADMIN, confirm_name="Tienda Norte")

    gone = norte.get("/panel/inbox", follow_redirects=False)
    assert gone.status_code == 303 and "/panel/login" in gone.headers["location"]
    again = TestClient(app).post(
        "/panel/login", data={"email": NORTE, "password": NORTE_PASSWORD}, follow_redirects=False
    )
    assert again.status_code != 303  # su usuario ya no existe
    # Su chat tampoco.
    assert TestClient(app).get(f"/c/{slug}").status_code == 404
    assert TestClient(app).post("/chat/session", params={"canal": slug}).status_code == 503
    # El nombre y el email quedan libres para crear la empresa de nuevo.
    create_company(world.conn, "Tienda Norte", NORTE, NORTE_PASSWORD)


def test_la_auditoria_de_casos_solo_se_borra_con_la_empresa_entera(world: World) -> None:  # noqa: F811
    """`audit_events` sigue siendo de solo inserción: borrar eventos sueltos falla, también
    para el dueño de la base. Solo se van cuando se borra su empresa."""
    conn = world.conn
    _, tenant_id = fill_norte(world)
    conn.execute("savepoint antes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from audit_events where tenant_id = %s", (tenant_id,))
    conn.execute("rollback to savepoint antes")
    # Marcar el borrado de una empresa no deja borrar la auditoría de otra.
    conn.execute("select set_config('app.deleting_tenant', %s, true)", (str(uuid.uuid4()),))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from audit_events where tenant_id = %s", (tenant_id,))
    conn.execute("rollback to savepoint antes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("update audit_events set kind = 'alterado' where tenant_id = %s", (tenant_id,))
