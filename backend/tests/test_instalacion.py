"""Hito 9, tarea 6 — test de aceptación: instalación (decisión 053).

El snippet para copiar ya estaba (decisión 031). Esta tarea suma, al lado del código, guías
cortas para Shopify, WordPress y Tiendanube, y cómo sumar WhatsApp; también en
`docs/instalacion.md` para mandarlas por email.
"""

import re
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.companies.create import create_company
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

DOCS = Path(__file__).resolve().parents[2] / "docs"
NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"


def test_la_guia_de_instalacion_cubre_cada_plataforma() -> None:
    text = (DOCS / "instalacion.md").read_text(encoding="utf-8")
    for platform in ("Shopify", "WordPress", "Tiendanube", "WhatsApp"):
        assert f"## {platform}" in text
    assert "theme.liquid" in text and "Dominios permitidos" in text


@pytest.fixture
def client(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    test_client = TestClient(app)
    test_client.post("/panel/login", data={"email": NORTE, "password": NORTE_PASSWORD})
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


def test_las_guias_estan_al_lado_del_codigo_para_copiar(client: TestClient) -> None:
    page = client.get("/panel/deploy").text
    snippet = page.index('id="snippet"')
    for platform in ("Shopify", "WordPress", "Tiendanube", "Cualquier sitio"):
        found = re.search(rf"<summary>[^<]*{platform}", page[snippet:])
        assert found is not None, platform
