"""Hito 13, tarea 2 — test de aceptación: el panel en inglés por defecto, con selector
English/Español por usuario, y textos claros, sin jerga técnica (decisión 085).

Recorre cada pantalla de una empresa (con conversaciones, una aprobación, tipos de caso y
acciones) en los dos idiomas:

- en inglés no queda texto en español, y en español no queda texto en inglés;
- en ningún idioma aparece jerga interna: tenant, RAG, guardián, embeddings, evals, onboarding,
  "tipo de caso" o "herramienta" (para el cliente son "topics" y "actions"), ni "asistente"
  (el producto es un agente).
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
from simulated.stack import build_simulated_stack
from test_panel_ingles import SPANISH, _harbor_data

from app.companies.create import create_company
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

OWNER, PASSWORD = "maya@harborsupply.example", "clave-de-harbor-supply"
PAGES = [
    "home",
    "inbox",
    "inbox/approvals",
    "inbox/drafts",
    "customers",
    "agent",
    "agent/actions",
    "agent/topics",
    "agent/voice",
    "agent/sales",
    "agent/language",
    "knowledge",
    "deploy",
    "test",
    "test/playground",
    "test/simulations",
    "test/improve",
    "insights",
    "insights/results",
    "settings",
    "settings/usage",
    "settings/spending",  # decisión 102
    "settings/data",
]
ENGLISH = re.compile(
    r"\b(?:the|your|you|and|with|this|that|for|from|of|is|are|it|on|or|yes|save|delete|"
    r"agent|customers?|settings|knowledge|approve|reject|waiting|none|yet|here|when|what|how|"
    r"upload|connect|add|open|test|turn|off|choose|send)\b",
    re.IGNORECASE,
)
JARGON_EN = re.compile(
    r"\b(?:tenants?|RAG|guardian|embeddings?|evals?|onboarding|case types?|tools?|assistant|"
    r"LLM|prompt)\b",
    re.IGNORECASE,
)
JARGON_ES = re.compile(
    r"\b(?:tenants?|RAG|guardián|guardian|embeddings?|evals?|onboarding|tipos? de caso|"
    r"herramientas?|asistente|LLM|prompt)\b",
    re.IGNORECASE,
)


class _Visible(HTMLParser):
    """El texto de la interfaz, sin lo que escribió el cliente o la empresa: mensajes, campos,
    código, opciones, nombres propios marcados con `translate="no"` y datos entre comillas."""

    SKIP_TAGS = {"script", "style", "textarea", "pre", "option", "code", "title"}
    SKIP_CLASSES = ("msg", "mono", "data")
    VOID = {"br", "input", "meta", "link", "img", "hr", "source", "wbr", "path"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[bool] = []
        self.texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return
        found = dict(attrs)
        classes = (found.get("class") or "").split()
        skip = tag in self.SKIP_TAGS or any(c in classes for c in self.SKIP_CLASSES)
        skip = skip or found.get("translate") == "no"
        self.stack.append(skip or (bool(self.stack) and self.stack[-1]))

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not (self.stack and self.stack[-1]) and not text.startswith("“"):
            self.texts.append(text)


def visible(markup: str) -> list[str]:
    parser = _Visible()
    parser.feed(markup)
    return parser.texts


@dataclass
class World:
    conn: psycopg.Connection
    tenant: uuid.UUID
    case_id: uuid.UUID


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")  # el producto: inglés por defecto
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Harbor Supply", OWNER, PASSWORD)
    case_id = _harbor_data(conn, stack, company.tenant_id)
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


def login() -> TestClient:
    client = TestClient(app)
    response = client.post("/panel/login", data={"email": OWNER, "password": PASSWORD})
    assert response.status_code == 200
    return client


def _urls(world: World) -> list[str]:
    return [f"/panel/{p}" for p in PAGES] + [f"/panel/casos/{world.case_id}"]


def _problems(client: TestClient, urls: list[str], *checks: re.Pattern[str]) -> dict[str, list]:
    found: dict[str, list[str]] = {}
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        bad = [t for t in visible(response.text) if any(c.search(t) for c in checks)]
        if bad:
            found[url] = bad
    return found


def test_el_panel_sale_en_ingles_por_defecto(world: World) -> None:
    client = login()
    assert '<html lang="en">' in client.get("/panel/home").text
    assert '<html lang="en">' in TestClient(app).get("/panel/login").text
    assert _problems(client, _urls(world), SPANISH, JARGON_EN) == {}


def test_cada_usuario_elige_espanol_y_queda_guardado(world: World) -> None:
    client = login()
    home = client.get("/panel/home").text
    token = re.search(r'name="csrf" value="([^"]+)"', home)
    assert token is not None and 'lang="es" translate="no">Español<' in home
    client.post("/panel/preferences", data={"lang": "es", "csrf": token.group(1)})
    again = login()  # otra sesión del mismo usuario
    assert '<html lang="es">' in again.get("/panel/home").text
    assert _problems(again, _urls(world), ENGLISH, JARGON_ES) == {}


def test_el_login_y_el_registro_en_los_dos_idiomas(world: World) -> None:
    client = TestClient(app)
    for url in ("/panel/login", "/registro"):
        assert _problems(client, [url], SPANISH, JARGON_EN) == {}, url
    client.get("/panel/login?lang=es")
    for url in ("/panel/login", "/registro"):
        page = client.get(url).text
        assert '<html lang="es">' in page, url
        assert _problems(client, [url], ENGLISH, JARGON_ES) == {}, url
