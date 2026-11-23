"""Hito 11, tarea 3 — test de aceptación de Primeros pasos (decisión 074).

En la portada del panel (Casos), una lista con el progreso real de la empresa: documentos, reglas,
marca, prueba en el playground, widget instalado y salir en vivo. Cada paso tiene su enlace y se
marca solo cuando la empresa lo hizo. La lista desaparece cuando está todo hecho.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import SimulatedStack, build_simulated_stack
from test_panel_ingles import spanish_left

from app import i18n
from app.channels.web import widget
from app.channels.web.routes import WebDeps, web_deps
from app.companies import first_steps, rules
from app.companies.create import Company, create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth, english
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
OWNER, PASSWORD = "lu@tiendasur.example", "clave-de-tienda-sur"
LINKS = [
    "/panel/knowledge",
    "/panel/agent",
    "/panel/deploy#widget",
    "/panel/test/playground",
    "/panel/test",
]


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    company: Company

    @property
    def tenant(self) -> uuid.UUID:
        return self.company.tenant_id


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Tienda Sur", OWNER, PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent
    )
    try:
        yield World(conn, stack, company)
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def done_keys(world: World) -> list[str]:
    return [s.key for s in first_steps.steps(world.conn, world.tenant) if s.done]


def test_una_empresa_nueva_ve_los_seis_pasos_con_su_enlace(world: World) -> None:
    page = login(OWNER, PASSWORD).get("/panel/home").text
    assert "Primeros pasos" in page and "0/6" in page
    for title in (
        "Cargá tus documentos",
        "Definí las reglas de atención",
        "Configurá tu marca",
        "Probá el asistente en el playground",
        "Instalá el widget en tu sitio",
        "Salí en vivo",
    ):
        assert title in page, title
    for link in LINKS:
        assert f'href="{link}"' in page, link
    assert done_keys(world) == []


def test_cada_paso_se_marca_con_el_progreso_real(world: World) -> None:
    tenant = world.tenant
    with tenant_scope(world.conn, tenant):
        world.conn.execute(
            "insert into documents (tenant_id, title, source) values (%s, 'Envíos', 'envios.md')",
            (tenant,),
        )
    assert done_keys(world) == ["documentos"]

    rules.save(world.conn, tenant, None, "equipo@tiendasur.example")
    widget.save(
        world.conn,
        tenant,
        name="Tienda Sur",
        accent="#123456",
        domains="tiendasur.example",
        suggestions="",
    )
    assert done_keys(world) == ["documentos", "reglas", "marca"]

    # El playground: el chat de la empresa respondió un mensaje.
    api = TestClient(app)
    slug = world.company.chat_slug
    session = api.post(f"/chat/session?canal={slug}").json()["session"]
    assert api.post("/chat/messages", json={"session": session, "text": "Hola"}).status_code == 200
    assert "playground" in done_keys(world)

    # El widget instalado: lo pidió desde uno de sus dominios (aunque no esté en vivo).
    api.get(f"/chat/widget/config?canal={slug}", headers={"Origin": "https://otro.example"})
    assert "widget" not in done_keys(world)
    api.get(f"/chat/widget/config?canal={slug}", headers={"Origin": "https://tiendasur.example"})
    assert "widget" in done_keys(world)

    page = login(OWNER, PASSWORD).get("/panel/home").text
    assert "5/6" in page

    with tenant_scope(world.conn, tenant):
        world.conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"live": True}), tenant),
        )
    assert len(done_keys(world)) == 6
    # Todo hecho: la lista desaparece.
    assert "Primeros pasos" not in login(OWNER, PASSWORD).get("/panel/home").text


def test_no_aparece_en_la_demo(world: World) -> None:
    assert "Primeros pasos" not in login(ADMIN, ADMIN_PASSWORD).get("/panel/home").text


def test_no_aparece_a_una_persona_del_equipo(world: World) -> None:
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "insert into users (tenant_id, email, role, password_hash)"
            " values (%s, %s, 'agent', %s)",
            (world.tenant, "ana@tiendasur.example", auth.hash_password("clave-de-ana-123")),
        )
    page = login("ana@tiendasur.example", "clave-de-ana-123").get("/panel/home").text
    assert "Primeros pasos" not in page


def test_en_ingles(world: World) -> None:
    i18n.set_language(world.conn, world.tenant, "en")
    page = login(OWNER, PASSWORD).get("/panel/home").text
    assert "First steps" in page and "Upload your documents" in page
    assert spanish_left(page) == []
    assert re.search(r"0/6</span>\s*<span>steps done", page)
