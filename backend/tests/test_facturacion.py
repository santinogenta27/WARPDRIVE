"""Hito 7, tarea 2 (tarea 22 de la spec) — test de aceptación: el conteo para facturar coincide
con la regla de cierre.

"Se cuenta como resuelto autónomamente si ningún humano intervino y no se reabre en 7 días. Esa
es la unidad de cobro." El operador ve, por empresa y por mes, cuántos casos se cobran, con el
detalle caso por caso y la exportación a CSV. Decisión 032.
"""

import csv
import io
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.cases import billing
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"
ART = timezone(timedelta(hours=-3))
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=ART)  # "hoy" de estos tests


def at(day: int, month: int = 11, hour: int = 10) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=ART)


def add_case(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    closed: datetime | None,
    *,
    by: str | None = "agent",
    reopened: bool = False,
    status: str = "resolved",
    archived: bool = False,
    case_type: str | None = None,
) -> uuid.UUID:
    with tenant_scope(conn, tenant_id):
        contact = conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (tenant_id, uuid.uuid4().hex),
        ).fetchone()
        assert contact is not None
        type_id = None
        if case_type:
            found = conn.execute(
                "select id from case_types where name = %s", (case_type,)
            ).fetchone()
            type_id = found[0] if found else None
        row = conn.execute(
            "insert into cases (tenant_id, contact_id, case_type_id, status, resolved_by,"
            " reopened, closed_at, created_at, archived_at) values (%s, %s, %s, %s, %s, %s, %s,"
            " %s, %s) returning id",
            (
                tenant_id,
                contact[0],
                type_id,
                status,
                by,
                reopened,
                closed,
                (closed or NOW) - timedelta(hours=2),
                NOW if archived else None,
            ),
        ).fetchone()
    assert row is not None
    case_id: uuid.UUID = row[0]
    return case_id


@pytest.fixture
def world(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[psycopg.Connection, SimulatedStack, uuid.UUID]]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr(billing, "current_time", lambda: NOW)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    norte = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD).tenant_id
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
    )
    try:
        yield conn, stack, norte
    finally:
        app.dependency_overrides.clear()


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


def seed(conn: psycopg.Connection, acme: uuid.UUID, norte: uuid.UUID) -> dict[str, uuid.UUID]:
    """Casos de noviembre de 2026 (hoy es el 20) y de los meses vecinos."""
    return {
        # Acme, noviembre
        "cobrable_1": add_case(conn, acme, at(3), case_type="reembolso"),
        "cobrable_2": add_case(conn, acme, at(13, hour=11)),  # justo 7 días y 1 hora
        "en_ventana": add_case(conn, acme, at(13, hour=13)),  # 6 días y 23 horas
        "ayer": add_case(conn, acme, at(19)),
        "con_persona": add_case(conn, acme, at(5), by="human"),
        "reabierto": add_case(conn, acme, at(6), reopened=True),  # se volvió a resolver
        "reabierto_humano": add_case(conn, acme, at(7), by="human", reopened=True),
        # Acme, lo que no entra en noviembre
        "octubre": add_case(conn, acme, at(31, month=10, hour=23)),
        "diciembre": add_case(conn, acme, datetime(2026, 12, 1, 0, 30, tzinfo=ART)),
        "abierto": add_case(conn, acme, None, by=None, status="open"),
        "derivado": add_case(conn, acme, None, by=None, status="escalated"),
        "reabierto_abierto": add_case(conn, acme, None, by=None, status="open", reopened=True),
        "archivado": add_case(conn, acme, at(4), archived=True),
        # Tienda Norte, noviembre
        "norte": add_case(conn, norte, at(2)),
    }


def test_el_conteo_del_mes_coincide_con_la_regla_de_cierre(
    world: tuple[psycopg.Connection, SimulatedStack, uuid.UUID],
) -> None:
    conn, stack, norte = world
    acme = stack.seed.tenant_id
    ids = seed(conn, acme, norte)

    period = billing.period_summary(conn, acme, 2026, 11)
    assert period == {
        "cobrables": 2,  # resueltos por el asistente, sin reabrir, con los 7 días cumplidos
        "en_ventana": 2,  # resueltos por el asistente hace menos de 7 días
        "con_persona": 1,  # intervino una persona
        "reabiertos": 2,  # el cliente volvió por lo mismo
    }
    states = {c.case_id: c.state for c in billing.period_cases(conn, acme, 2026, 11)}
    assert states == {
        ids["cobrable_1"]: "cobrable",
        ids["cobrable_2"]: "cobrable",
        ids["en_ventana"]: "en_ventana",
        ids["ayer"]: "en_ventana",
        ids["con_persona"]: "con_persona",
        ids["reabierto"]: "reabierto",
        ids["reabierto_humano"]: "reabierto",
    }
    # El mes es el de Buenos Aires: el 31/10 a las 23 es octubre aunque en UTC ya sea noviembre.
    assert [c.case_id for c in billing.period_cases(conn, acme, 2026, 10)] == [ids["octubre"]]
    assert [c.case_id for c in billing.period_cases(conn, acme, 2026, 12)] == [ids["diciembre"]]
    assert billing.period_summary(conn, acme, 2026, 10)["cobrables"] == 1
    # Cada empresa cuenta solo lo suyo.
    assert billing.period_summary(conn, norte, 2026, 11) == {
        "cobrables": 1,
        "en_ventana": 0,
        "con_persona": 0,
        "reabiertos": 0,
    }
    by_company = {c.tenant_id: c for c in billing.period_by_company(conn, 2026, 11)}
    assert by_company[acme].name == "Acme Store" and by_company[acme].counts == period
    assert by_company[norte].counts["cobrables"] == 1


def test_un_caso_resuelto_por_el_agente_y_reabierto_deja_de_cobrarse(
    world: tuple[psycopg.Connection, SimulatedStack, uuid.UUID],
) -> None:
    """La regla viva: el agente resuelve, el cliente vuelve por lo mismo dentro de los 7 días y
    el caso se reabre. Ya no se cobra, aunque después se resuelva de nuevo."""
    conn, stack, _ = world
    acme = stack.seed.tenant_id
    case_id = add_case(conn, acme, at(10), case_type="reembolso")
    assert billing.period_summary(conn, acme, 2026, 11)["cobrables"] == 1
    billing.reopen(conn, acme, case_id)
    assert billing.period_cases(conn, acme, 2026, 11) == []  # abierto: no es de ningún mes
    with tenant_scope(conn, acme):
        conn.execute(
            "update cases set status = 'resolved', resolved_by = 'agent', closed_at = %s"
            " where id = %s",
            (at(11), case_id),
        )
    again = billing.period_summary(conn, acme, 2026, 11)
    assert again["cobrables"] == 0 and again["reabiertos"] == 1


def test_el_operador_ve_la_facturacion_por_empresa_y_por_mes(
    world: tuple[psycopg.Connection, SimulatedStack, uuid.UUID],
) -> None:
    conn, stack, norte = world
    acme = stack.seed.tenant_id
    ids = seed(conn, acme, norte)
    admin = login(ADMIN, ADMIN_PASSWORD)

    assert "Facturación" in admin.get("/panel/inbox").text  # está en el menú del operador
    page = admin.get("/panel/operator/billing", params={"mes": "2026-11"})
    assert page.status_code == 200
    assert "Noviembre de 2026" in page.text
    assert "Acme Store" in page.text and "Tienda Norte" in page.text
    assert f"/panel/facturacion/{acme}?mes=2026-11" in page.text
    assert "mes=2026-10" in page.text and "mes=2026-12" in page.text  # mes anterior y siguiente
    # Sin mes, el actual. Un mes mal escrito no rompe: muestra el actual.
    assert "Noviembre de 2026" in admin.get("/panel/operator/billing").text
    assert admin.get("/panel/operator/billing", params={"mes": "2026-13"}).status_code == 200

    detail = admin.get(f"/panel/facturacion/{acme}", params={"mes": "2026-11"})
    assert detail.status_code == 200 and "Acme Store" in detail.text
    for name in ("cobrable_1", "en_ventana", "con_persona", "reabierto"):
        assert str(ids[name])[:8] in detail.text, name
    for name in ("octubre", "diciembre", "abierto", "archivado", "norte"):
        assert str(ids[name])[:8] not in detail.text, name
    assert "Reembolso" in detail.text  # el tipo de caso, legible
    assert admin.get(f"/panel/facturacion/{uuid.uuid4()}").status_code == 404


def test_la_facturacion_se_exporta_a_csv(
    world: tuple[psycopg.Connection, SimulatedStack, uuid.UUID],
) -> None:
    conn, stack, norte = world
    acme = stack.seed.tenant_id
    ids = seed(conn, acme, norte)
    admin = login(ADMIN, ADMIN_PASSWORD)

    summary = admin.get("/panel/facturacion/exportar.csv", params={"mes": "2026-11"})
    assert summary.status_code == 200
    assert summary.headers["content-type"].startswith("text/csv")
    assert 'filename="facturacion-2026-11.csv"' in summary.headers["content-disposition"]
    assert summary.content.startswith(b"\xef\xbb\xbf")  # BOM: Excel lo abre con tildes
    rows = list(csv.DictReader(io.StringIO(summary.content.decode("utf-8-sig"))))
    acme_row = next(r for r in rows if r["id_de_empresa"] == str(acme))
    assert acme_row["empresa"] == "Acme Store"
    assert acme_row["mes"] == "2026-11" and acme_row["casos_a_cobrar"] == "2"
    assert acme_row["dentro_de_los_7_dias"] == "2" and acme_row["con_una_persona"] == "1"
    assert acme_row["reabiertos"] == "2" and acme_row["resueltos_en_total"] == "7"
    assert next(r for r in rows if r["id_de_empresa"] == str(norte))["casos_a_cobrar"] == "1"

    detail = admin.get(f"/panel/facturacion/{acme}/exportar.csv", params={"mes": "2026-11"})
    assert detail.status_code == 200
    assert "facturacion-acme-store-2026-11.csv" in detail.headers["content-disposition"]
    cases = list(csv.DictReader(io.StringIO(detail.content.decode("utf-8-sig"))))
    assert len(cases) == 7
    by_id = {r["caso"]: r for r in cases}
    assert by_id[str(ids["cobrable_1"])]["se_cobra"] == "sí"
    assert by_id[str(ids["cobrable_1"])]["tipo_de_caso"] == "reembolso"
    assert by_id[str(ids["cobrable_1"])]["resuelto"] == "2026-11-03 10:00"
    assert by_id[str(ids["en_ventana"])]["se_cobra"] == "todavía no"
    assert by_id[str(ids["con_persona"])]["se_cobra"] == "no"
    assert by_id[str(ids["reabierto"])]["estado"] == "reabierto"
    assert str(ids["norte"]) not in detail.text
    # Sin datos personales: ni mensajes ni teléfonos.
    assert set(cases[0]) == {
        "caso",
        "tipo_de_caso",
        "abierto",
        "resuelto",
        "resuelto_por",
        "estado",
        "se_cobra",
    }


def test_solo_el_operador_ve_la_facturacion(
    world: tuple[psycopg.Connection, SimulatedStack, uuid.UUID],
) -> None:
    conn, stack, norte = world
    seed(conn, stack.seed.tenant_id, norte)
    user = login(NORTE, NORTE_PASSWORD)
    assert "Facturación" not in user.get("/panel/inbox").text
    for path in (
        "/panel/operator/billing",
        "/panel/facturacion/exportar.csv",
        f"/panel/facturacion/{stack.seed.tenant_id}",
        f"/panel/facturacion/{norte}",
        f"/panel/facturacion/{norte}/exportar.csv",
    ):
        response = user.get(path)
        assert response.status_code == 403, path
        assert "Acme Store" not in response.text
    anonymous = TestClient(app).get("/panel/operator/billing", follow_redirects=False)
    assert anonymous.status_code == 303 and "/panel/login" in anonymous.headers["location"]
