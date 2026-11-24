"""Hito 13, tarea 7 — test de aceptación: calidad visual y de uso (decisión 089).

- Accesible en cada pantalla: un solo título principal, cada control con su etiqueta y cada
  botón con texto.
- Confirmación antes de lo peligroso: aprobar un reembolso, pausar el agente, borrar.
- Estados vacíos con explicación y un botón; páginas de error con una salida.
- Tema claro por defecto y oscuro a elección del usuario.
- Lo que un cliente no necesita (latencias, proveedores, historial de pruebas) queda para el
  operador.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from html.parser import HTMLParser

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import build_simulated_stack
from test_panel_ingles import _harbor_data

from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
OWNER, PASSWORD = "maya@harborsupply.example", "clave-de-harbor-supply"
NEW, NEW_PASSWORD = "sol@tiendanueva.example", "clave-de-tienda-nueva"
PAGES = [
    "home", "inbox", "inbox/approvals", "inbox/drafts", "customers", "agent", "agent/actions",
    "agent/topics", "agent/voice", "agent/sales", "agent/language", "knowledge", "deploy",
    "test", "test/playground", "test/simulations", "test/improve", "insights",
    "insights/results", "settings", "settings/usage", "settings/data",
]  # fmt: skip


@dataclass
class World:
    conn: psycopg.Connection
    tenant: uuid.UUID
    case_id: uuid.UUID


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Harbor Supply", OWNER, PASSWORD)
    case_id = _harbor_data(conn, stack, company.tenant_id)
    create_company(conn, "Tienda Nueva", NEW, NEW_PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield World(conn, company.tenant_id, case_id)
    finally:
        app.dependency_overrides.clear()


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/panel/login", data={"email": email, "password": password}).is_success
    return client


class Audit(HTMLParser):
    """Lo que revisa un lector de pantalla: títulos, etiquetas de los controles y botones."""

    def __init__(self) -> None:
        super().__init__()
        self.h1 = 0
        self.labels: set[str] = set()
        self.controls: list[tuple[str, dict[str, str | None], bool]] = []
        self.in_label = 0
        self.buttons: list[str] = []
        self.button: list[str] | None = None
        self.button_attrs: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "h1":
            self.h1 += 1
        elif tag == "label":
            self.in_label += 1
            if found.get("for"):
                self.labels.add(str(found["for"]))
        elif tag in ("input", "select", "textarea"):
            if found.get("type") not in ("hidden", "submit") and "disabled" not in found:
                self.controls.append((tag, found, self.in_label > 0))
        elif tag == "button":
            self.button = []
            self.button_attrs = found

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self.in_label = max(0, self.in_label - 1)
        elif tag == "button" and self.button is not None:
            text = " ".join("".join(self.button).split())
            self.buttons.append(text or str(self.button_attrs.get("aria-label") or ""))
            self.button = None

    def handle_data(self, data: str) -> None:
        if self.button is not None:
            self.button.append(data)

    def problems(self) -> list[str]:
        found = [] if self.h1 == 1 else [f"{self.h1} h1"]
        for tag, attrs, wrapped in self.controls:
            labeled = (
                wrapped
                or attrs.get("aria-label")
                or (attrs.get("id") and attrs["id"] in self.labels)
                or attrs.get("hidden") is not None
            )
            if not labeled:
                found.append(f"{tag} sin etiqueta: {attrs.get('name') or attrs.get('id')}")
        found += ["botón sin texto" for text in self.buttons if not text]
        return found


@pytest.mark.parametrize("slug", PAGES)
def test_cada_pantalla_es_accesible(world: World, slug: str) -> None:
    page = login(OWNER, PASSWORD).get(f"/panel/{slug}").text
    main = re.search(r'<main id="main">(.*)</main>', page, re.S)
    assert main is not None
    audit = Audit()
    audit.feed(main.group(1))
    assert audit.problems() == [], slug
    assert 'name="viewport"' in page and ":focus-visible" in page


def test_lo_peligroso_pide_confirmacion(world: World) -> None:
    client = login(OWNER, PASSWORD)
    home = client.get("/panel/home").text
    assert '<dialog id="confirm"' in home
    refund = re.search(r'value="aprobar"\s+data-confirm="Approve this refund\?', home)
    assert refund is not None  # aprobar un reembolso
    for url in ("/panel/inbox", "/panel/inbox/approvals", f"/panel/casos/{world.case_id}"):
        assert 'data-confirm="Approve this refund?' in client.get(url).text, url
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"live": True}), world.tenant),
        )
    assert 'data-confirm="Pause your agent?' in client.get("/panel/home").text
    assert 'data-confirm="Pause your agent?' in client.get("/panel/deploy").text
    data = client.get("/panel/settings/data").text
    assert 'data-confirm="Delete your company and all its data?' in data


def test_estados_vacios_con_explicacion_y_accion(world: World) -> None:
    client = login(NEW, NEW_PASSWORD)
    for slug in ("inbox", "inbox/approvals", "inbox/drafts", "customers", "test/improve"):
        page = client.get(f"/panel/{slug}").text
        empty = re.search(r'<div class="card empty"[^>]*>(.*?)</div>', page, re.S)
        assert empty is not None, slug
        assert "<b>" in empty.group(1) and 'class="btn' in empty.group(1), slug
    missing = client.get(f"/panel/casos/{uuid.uuid4()}")
    assert missing.status_code == 404 and 'href="/panel/home">Go to Home<' in missing.text


def test_tema_claro_por_defecto_y_oscuro_a_eleccion(world: World) -> None:
    client = login(OWNER, PASSWORD)
    home = client.get("/panel/home").text
    assert '<html lang="en">' in home and "data-theme" not in home.split("<head>")[0]
    assert ":root[data-theme=dark]" in home
    token = re.search(r'name="csrf" value="([^"]+)"', home)
    assert token is not None
    client.post("/panel/preferences", data={"csrf": token.group(1), "theme": "dark"})
    assert '<html lang="en" data-theme="dark">' in client.get("/panel/home").text
    # Queda guardado en el usuario: el próximo login también.
    assert 'data-theme="dark"' in login(OWNER, PASSWORD).get("/panel/home").text


def test_insights_sin_lo_tecnico_para_el_cliente(world: World) -> None:
    customer = login(OWNER, PASSWORD).get("/panel/insights").text
    for technical in ("Latency", "Test history", "OpenTelemetry", "backup provider"):
        assert technical not in customer, technical
    assert "Customer satisfaction" in customer or "CSAT" in customer
    operator = login(ADMIN, ADMIN_PASSWORD).get("/panel/insights").text
    assert "Latencia" in operator or "Latency" in operator
