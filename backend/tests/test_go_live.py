"""Hito 9, tarea 8 — test de aceptación: probar antes de salir (decisión 056).

- "Salir en vivo" se habilita solo si el reporte de preparación (evals generados con el
  historial de la empresa) pasa el umbral y frena todos los casos adversariales.
- Antes de salir, el widget no atiende en el sitio de la empresa; la página propia y el
  playground del panel sí, para probar.
- Las empresas de demo están siempre en vivo.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import build_simulated_stack

from app.companies import go_live
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps


def _report(conn: psycopg.Connection, tenant: uuid.UUID, estimate: float, passed: int) -> None:
    report = {
        "resolucion_estimada": estimate,
        "tipos": [],
        "adversariales": {"total": 4, "superados": passed, "fallas": []},
    }
    with tenant_scope(conn, tenant):
        conn.execute(
            # La hora real: en la misma transacción, now() es igual para todos.
            "insert into onboarding_runs (tenant_id, status, report, updated_at)"
            " values (%s, 'done', %s, clock_timestamp())",
            (tenant, Jsonb(report)),
        )


def _norte(conn: psycopg.Connection) -> uuid.UUID:
    return create_company(
        conn, "Tienda Norte", "ana@tiendanorte.example", "clave-norte-1"
    ).tenant_id


def test_sin_reporte_no_se_puede_salir(conn: psycopg.Connection) -> None:
    tenant = _norte(conn)
    found = go_live.readiness(conn, tenant)
    assert not found.ready and not found.live and "reporte" in found.reasons[0]
    with pytest.raises(go_live.GoLiveError):
        go_live.set_live(conn, tenant, True)


def test_el_umbral_y_los_adversariales_deciden(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GO_LIVE_MIN_RESOLUTION", "0.7")
    tenant = _norte(conn)
    _report(conn, tenant, 0.65, 4)
    assert not go_live.readiness(conn, tenant).ready
    _report(conn, tenant, 0.8, 3)
    assert not go_live.readiness(conn, tenant).ready
    _report(conn, tenant, 0.8, 4)
    assert go_live.readiness(conn, tenant).ready
    go_live.set_live(conn, tenant, True)
    assert go_live.is_live(conn, tenant)
    go_live.set_live(conn, tenant, False)
    assert not go_live.is_live(conn, tenant)


def test_la_demo_esta_siempre_en_vivo(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    assert go_live.is_live(conn, stack.seed.tenant_id)


# -- el panel: playground y botón ------------------------------------------------------------
@pytest.fixture
def norte_panel(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, uuid.UUID]]:
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    tenant = _norte(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    client = TestClient(app)
    client.post(
        "/panel/login", data={"email": "ana@tiendanorte.example", "password": "clave-norte-1"}
    )
    try:
        yield client, tenant
    finally:
        app.dependency_overrides.clear()


def test_el_playground_y_el_boton_de_salir_en_vivo(
    conn: psycopg.Connection, norte_panel: tuple[TestClient, uuid.UUID]
) -> None:
    client, tenant = norte_panel
    playground = client.get("/panel/probar")
    assert playground.status_code == 200 and "/panel/probar/live" in playground.text
    _report(conn, tenant, 0.5, 4)
    page = client.get("/panel/test").text
    assert "Salir en vivo" in page and "disabled" in page
    _report(conn, tenant, 0.9, 4)
    page = client.get("/panel/test").text
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    client.post("/panel/salir-en-vivo", data={"csrf": token.group(1), "activo": "si"})
    assert go_live.is_live(conn, tenant)
