"""Hito 13, tarea 1 — test de aceptación: identidad y navegación del panel (decisión 085).

- El panel se llama "Enterprise Data AI · AI Support & Sales Agent"; la empresa del cliente es una
  etiqueta, no el título. Nada de "Panel del equipo" ni textos de demo.
- Barra lateral con 9 secciones, en orden: Home, Inbox, Customers, Agent, Knowledge, Deploy,
  Test & Improve, Insights y Settings. Cada pantalla de antes vive en una de ellas.
- Zona de operador (Companies, Trials, Billing, Behind the scenes) solo para el operador.
- Las direcciones de antes llevan a la pantalla nueva.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
OWNER, OWNER_PASSWORD = "owner@brisahome.example", "clave-de-brisa-home"
MEMBER, MEMBER_PASSWORD = "team@brisahome.example", "clave-del-equipo-brisa"
SECTIONS = [
    ("Home", "/panel/home"),
    ("Inbox", "/panel/inbox"),
    ("Customers", "/panel/customers"),
    ("Agent", "/panel/agent"),
    ("Knowledge", "/panel/knowledge"),
    ("Deploy", "/panel/deploy"),
    ("Test &amp; Improve", "/panel/test"),
    ("Insights", "/panel/insights"),
    ("Settings", "/panel/settings"),
]
TABS = {
    "/panel/inbox": ["Conversations", "Approvals", "Drafts"],
    "/panel/agent": ["Rules", "Actions", "Topics", "Brand &amp; voice", "Sales", "Language"],
    "/panel/test": ["Readiness", "Playground", "Simulations", "To improve"],
    "/panel/insights": ["Overview", "Results"],
    "/panel/settings": ["Team", "Usage &amp; model", "Your data"],
}
OPERATOR = ["/panel/operator/companies", "/panel/operator/trials", "/panel/operator/billing"]


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")  # el producto: inglés por defecto
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Brisa Home", OWNER, OWNER_PASSWORD)
    with tenant_scope(conn, company.tenant_id):
        conn.execute(
            "insert into users (tenant_id, email, role, password_hash)"
            " values (%s, %s, 'agent', %s)",
            (company.tenant_id, MEMBER, auth.hash_password(MEMBER_PASSWORD)),
        )
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield company.tenant_id
    finally:
        app.dependency_overrides.clear()


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/panel/home"
    return client


def sidebar(markup: str) -> str:
    found = re.search(r'<aside class="side".*?</aside>', markup, re.S)
    assert found is not None
    return found.group(0)


def test_identidad_del_producto(world: uuid.UUID) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    home = client.get("/panel/home").text
    assert "<title>Home · Enterprise Data AI</title>" in home
    side = sidebar(home)
    assert "<b>Enterprise Data AI</b>" in side and "AI Support &amp; Sales Agent" in side
    assert 'title="Company"' in side and "Brisa Home" in side  # la empresa, como etiqueta
    for banned in ("Panel del equipo", "Team dashboard", "tienda ficticia", "Demo de Enterprise"):
        assert banned not in home


def test_nueve_secciones_en_orden(world: uuid.UUID) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    side = sidebar(client.get("/panel/home").text)
    found = re.findall(r'<a href="(/panel/[a-z/]+)"[^>]*>.*?<span>([^<]+)</span>', side, re.S)
    assert [(label, url) for url, label in found] == SECTIONS
    for _, url in SECTIONS:
        response = client.get(url)
        assert response.status_code == 200, url
        assert "aria-current=page" in sidebar(response.text), url


@pytest.mark.parametrize("url", list(TABS))
def test_pestanas_de_cada_seccion(world: uuid.UUID, url: str) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    tabs = re.search(r'<nav class="tabs".*?</nav>', client.get(url).text, re.S)
    assert tabs is not None
    links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', tabs.group(0))
    assert [label for _, label in links] == TABS[url]
    for href, _ in links:
        assert client.get(href).status_code == 200, href


def test_el_cliente_no_ve_la_zona_del_operador(world: uuid.UUID) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    side = sidebar(client.get("/panel/home").text)
    assert "Operator" not in side and "/panel/operator" not in side
    for url in [*OPERATOR, "/panel/operator/behind"]:
        assert client.get(url).status_code == 403, url


def test_el_operador_tiene_su_zona_aparte(world: uuid.UUID) -> None:
    client = login(ADMIN, ADMIN_PASSWORD)
    side = sidebar(client.get("/panel/home").text)
    zone = side.split('<div class="group">Operator</div>', 1)
    assert len(zone) == 2
    for label in ("Companies", "Trials", "Billing", "Behind the scenes"):
        assert f"<span>{label}</span>" in zone[1]
    for url in [*OPERATOR, "/panel/operator/behind"]:
        assert client.get(url).status_code == 200, url


def test_una_persona_del_equipo_no_ve_la_configuracion(world: uuid.UUID) -> None:
    client = login(MEMBER, MEMBER_PASSWORD)
    side = sidebar(client.get("/panel/home").text)
    for label in ("Agent", "Knowledge", "Deploy", "Settings"):
        assert f"<span>{label}</span>" not in side
    for label in ("Home", "Inbox", "Customers", "Insights"):
        assert f"<span>{label}</span>" in side
    for url in ("/panel/agent", "/panel/knowledge", "/panel/deploy", "/panel/settings/data"):
        assert client.get(url).status_code == 403, url


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/panel/casos", "/panel/inbox"),
        ("/panel/aprobaciones", "/panel/inbox/approvals"),
        ("/panel/onboarding", "/panel/knowledge"),
        ("/panel/configuracion", "/panel/agent"),
        ("/panel/reporte", "/panel/test"),
        ("/panel/mejorar", "/panel/test/improve"),
        ("/panel/metricas", "/panel/insights"),
        ("/panel/resultados?mes=2026-10", "/panel/insights/results?mes=2026-10"),
        ("/panel/clientes", "/panel/customers"),
        ("/panel/equipo", "/panel/settings"),
        ("/panel/empresas", "/panel/operator/companies"),
    ],
)
def test_las_direcciones_de_antes_llevan_a_la_nueva(world: uuid.UUID, old: str, new: str) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    response = client.get(old, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == new


def test_cada_formulario_vuelve_a_su_pantalla(world: uuid.UUID) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    page = client.get("/panel/agent/language").text
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    saved = client.post(
        "/panel/configuracion/idioma",
        data={"idioma": "en", "csrf": token.group(1)},
        follow_redirects=False,
    )
    assert saved.headers["location"].startswith("/panel/agent/language?ok=")


def test_el_cliente_exporta_y_borra_sus_datos(world: uuid.UUID) -> None:
    client = login(OWNER, OWNER_PASSWORD)
    page = client.get("/panel/settings/data").text
    assert 'href="/panel/settings/data/export"' in page and "Type <b>Brisa Home</b>" in page
    exported = client.get("/panel/settings/data/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    wrong = client.post(
        "/panel/settings/data/delete", data={"nombre": "otra", "csrf": token.group(1)}
    )
    assert wrong.status_code == 200 and "/panel/settings/data" in str(wrong.url)
    deleted = client.post(
        "/panel/settings/data/delete",
        data={"nombre": "Brisa Home", "csrf": token.group(1)},
        follow_redirects=False,
    )
    assert deleted.headers["location"] == "/panel/login"
    assert (
        TestClient(app)
        .post("/panel/login", data={"email": OWNER, "password": OWNER_PASSWORD})
        .status_code
        == 401
    )
