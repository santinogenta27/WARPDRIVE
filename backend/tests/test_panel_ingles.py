"""Hito 8, tarea 2 — test de aceptación del panel en inglés (decisión 037).

El panel habla el idioma de la empresa de la sesión. Una empresa en inglés ve cada pantalla en
inglés, con el mismo diseño (el mismo HTML, otro texto): Casos, el detalle con la auditoría,
Aprobaciones, Onboarding, Reporte, Configuración y Métricas. Los datos de la empresa y de sus
clientes no se traducen. Acme, en español, no cambia.
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
from simulated.stack import SimulatedStack, build_simulated_stack

from app import i18n
from app.companies.create import create_company
from app.contacts import hash_address
from app.db.connection import tenant_scope
from app.main import app
from app.onboarding.toolgen import store_tools
from app.panel import auth, english
from app.panel.labels import action_label, money, type_label
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
HARBOR, HARBOR_PASSWORD = "maya@harborsupply.example", "clave-de-harbor-supply"
TYPE = """nombre: where_is_my_order
objetivo: Tell the customer where their order is and when it arrives
datos_necesarios: [order_id]
herramientas: [consultar_pedido]
condiciones:
  - If the order is late, apologize and give the new date.
cierre: Ask if there's anything else.
"""
PAGES = [
    "inbox",
    "inbox/approvals",
    "inbox/drafts",
    "home",
    "customers",
    "knowledge",
    "test",
    "test/improve",
    "test/simulations",
    "agent",
    "agent/actions",
    "agent/topics",
    "agent/voice",
    "agent/sales",
    "agent/language",
    "deploy",
    "insights",
    "insights/results",
    "settings",
    "settings/usage",
    "settings/data",
]


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    harbor: uuid.UUID
    case_id: uuid.UUID


def _harbor_data(conn: psycopg.Connection, stack: SimulatedStack, tenant: uuid.UUID) -> uuid.UUID:
    """Una empresa en inglés con un tipo de caso, herramientas, conversaciones y una aprobación
    pendiente: todo lo que muestran las pantallas."""
    acme = stack.agent._guardian.manifests(
        stack.seed.tenant_id, ["consultar_pedido", "iniciar_reembolso"]
    )
    english_descriptions = {
        "consultar_pedido": "Looks up the status and delivery date of a verified customer's order",
        "iniciar_reembolso": "Starts a refund for a delivered order of the verified customer",
    }
    store_tools(conn, tenant, [{**m, "description": english_descriptions[m["name"]]} for m in acme])
    with tenant_scope(conn, tenant):
        conn.execute("update tools set approved = true where name = 'consultar_pedido'")
        conn.execute(
            "insert into case_types (tenant_id, name, instructions, active, volume)"
            " values (%s, 'where_is_my_order', %s, true, 12)",
            (tenant, TYPE),
        )
        contact = conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (tenant, hash_address(f"web:panel-en-{uuid.uuid4().hex}")),
        ).fetchone()
    assert contact is not None
    stack.agent.handle_message(tenant, contact[0], "Hi there")
    stack.agent.handle_message(tenant, contact[0], "I want to talk to a person")
    with tenant_scope(conn, tenant):
        case = conn.execute("select id from cases order by created_at desc limit 1").fetchone()
        assert case is not None
        conn.execute(
            "insert into approvals (tenant_id, case_id, tool_id, params, resource_key)"
            " select %s, %s, id, %s, 'order_id=NW-4521' from tools"
            " where name = 'iniciar_reembolso'",
            (tenant, case[0], Jsonb({"order_id": "NW-4521", "amount": 189.5})),
        )
    case_id: uuid.UUID = case[0]
    return case_id


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Harbor Supply", HARBOR, HARBOR_PASSWORD)
    i18n.set_language(conn, company.tenant_id, "en")
    # El idioma del panel es el del usuario (decisión 085).
    auth.save_preferences(conn, company.tenant_id, HARBOR, {"language": "en"})
    case_id = _harbor_data(conn, stack, company.tenant_id)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield World(conn, stack, company.tenant_id, case_id)
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


class _Visible(HTMLParser):
    """El texto de la interfaz: sin scripts, estilos, campos, código, mensajes ni datos entre
    comillas (lo que escribió el cliente o el asistente)."""

    SKIP_TAGS = {"script", "style", "textarea", "pre", "option"}
    SKIP_CLASSES = ("msg", "mono")

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[bool] = []
        self.texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "input", "meta", "link", "img", "hr"}:
            return
        classes = dict(attrs).get("class") or ""
        skip = tag in self.SKIP_TAGS or any(c in classes.split() for c in self.SKIP_CLASSES)
        skip = skip or dict(attrs).get("translate") == "no"  # p. ej. el botón "Español"
        self.stack.append(skip or (bool(self.stack) and self.stack[-1]))

    def handle_endtag(self, tag: str) -> None:
        if tag not in {"br", "input", "meta", "link", "img", "hr"} and self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not (self.stack and self.stack[-1]) and not text.startswith("“"):
            self.texts.append(text)


SPANISH = re.compile(
    r"[áéíóúñ¿¡]|\b(?:de|del|la|las|los|el|que|con|para|por|una|tus?|acá|todavía|caso|casos"
    r"|pedidos?|herramientas?|clientes?|sin|aprobar|derivad\w*|riesgo|bajo|medio|alto|solo"
    r"|consulta|pide|verificad\w*|datos|modifica|tope|asistente|empresa|equipo|guardar|nuevo"
    r"|ver|editar|activo|inactivo|resuelto\w*|abierto\w*|mensajes?|respuesta|costo|llamadas"
    r"|tipos?|fuentes?|sistema|subir|archivo|nombre|marca|dominios?|idioma|pegá"
    r"|cambia|tiene|hay|esta|este|son|muestra|usa|uso|cada|tiempo|promedio|paso)\b",
    re.IGNORECASE,
)


def spanish_left(markup: str) -> list[str]:
    parser = _Visible()
    parser.feed(markup)
    return [t for t in parser.texts if SPANISH.search(t)]


# -- cada pantalla en inglés ------------------------------------------------------------------
@pytest.mark.parametrize("slug", PAGES)
def test_cada_pantalla_de_una_empresa_en_ingles_sale_en_ingles(world: World, slug: str) -> None:
    client = login(HARBOR, HARBOR_PASSWORD)
    response = client.get(f"/panel/{slug}")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert spanish_left(response.text) == []


def test_el_detalle_del_caso_y_su_auditoria_en_ingles(world: World) -> None:
    client = login(HARBOR, HARBOR_PASSWORD)
    page = client.get(f"/panel/casos/{world.case_id}").text
    assert spanish_left(page) == []
    for text in (
        "Step-by-step audit trail",
        "Audit chain intact",
        "Classified the case",
        "Handed off to a person",
        "Reason: The customer asked to talk to a person.",
        "Reply as a member of the team",
        "Refund · Order NW-4521 · $189.50",
        "Approve",
        "Reject",
        "Response time",
    ):
        assert text in page, text
    assert 'placeholder="Hi, this is the Harbor Supply team…"' in page


def test_textos_comunes_en_ingles(world: World) -> None:
    client = login(HARBOR, HARBOR_PASSWORD)
    inbox = client.get("/panel/inbox").text
    for text in ("Inbox", "Approvals", "Settings", "Insights", "Sign out"):
        assert f">{text}<" in inbox, text
    assert "Handed off to a person" in inbox and ">Handed off<" in inbox
    assert "· Channel" in inbox
    for url, text in (
        ("/panel/agent/topics", "Where is my order"),
        ("/panel/agent/actions", "Order lookup"),
        ("/panel/agent/actions", "Low risk · requires a verified identity · read only"),
        ("/panel/agent/actions", ">On<"),
        ("/panel/agent/actions", ">Off<"),
        ("/panel/agent/language", "Language your agent speaks"),
    ):
        assert text in client.get(url).text, text


def test_el_mismo_html_en_los_dos_idiomas(world: World) -> None:
    """Mismo diseño: las etiquetas y clases son las mismas, solo cambia el texto. El usuario
    elige el idioma en el menú y queda guardado."""
    client = login(HARBOR, HARBOR_PASSWORD)
    english_page = client.get("/panel/agent/actions").text
    token = re.search(r'name="csrf" value="([^"]+)"', english_page)
    assert token is not None
    client.post("/panel/preferences", data={"lang": "es", "csrf": token.group(1)})
    spanish_page = client.get("/panel/agent/actions").text

    def skeleton(markup: str) -> list[str]:
        return re.findall(r"<([a-z0-9]+)(?:\s+class=\"([^\"]*)\")?", markup)

    assert skeleton(english_page) == skeleton(spanish_page)
    assert '<html lang="es">' in spanish_page
    assert ">Acciones<" in spanish_page and ">Actions<" not in spanish_page
    # La preferencia queda en el usuario: el próximo login también sale en español.
    assert '<html lang="es">' in login(HARBOR, HARBOR_PASSWORD).get("/panel/home").text


def test_acme_sigue_en_espanol(world: World) -> None:
    client = login(ADMIN, ADMIN_PASSWORD)
    for slug in PAGES:
        page = client.get(f"/panel/{slug}").text
        assert '<html lang="es">' in page, slug
    assert ">Bandeja<" in client.get("/panel/inbox").text


def test_el_login_se_puede_ver_en_ingles() -> None:
    client = TestClient(app)
    spanish_login = client.get("/panel/login").text
    assert "Ingresar" in spanish_login and 'href="/panel/login?lang=en"' in spanish_login
    english_login = client.get("/panel/login?lang=en").text
    assert ">Sign in<" in english_login and ">Password<" in english_login
    assert '<html lang="en">' in english_login
    wrong = client.post(
        "/panel/login", data={"email": "nadie@example.com", "password": "x", "lang": "en"}
    )
    assert "Wrong email or password." in wrong.text


def test_los_avisos_al_guardar_salen_en_ingles(world: World) -> None:
    client = login(HARBOR, HARBOR_PASSWORD)
    page = client.get("/panel/configuracion").text
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    saved = client.post(
        "/panel/configuracion/idioma", data={"idioma": "en", "csrf": token.group(1)}
    )
    assert "Language saved: English." in saved.text


def test_montos_fechas_y_etiquetas() -> None:
    english.set_language("en")
    try:
        assert money(189.5) == "$189.50" and money(1250) == "$1,250"
        assert action_label("iniciar_cambio", {"order_id": "NW-4533", "new_size": "M"}) == (
            "Exchange · Order NW-4533 · New size: M"
        )
        assert action_label("ofrecer_cupon", {"order_id": "NW-4521", "percent": 10}) == (
            "Coupon · Order NW-4521 · 10% off"
        )
        assert type_label("where_is_my_order") == "Where is my order"
        assert type_label("conversacion_general") == "General conversation"
        assert english.english_date("12/11 14:05", "%d/%m %H:%M") == "Nov 12, 14:05"
        assert english.translate_text("12.345") == "12,345"
        assert english.translate_text("1,2 s") == "1.2 s"
    finally:
        english.set_language("es")
    assert money(35000) == "$35.000"
    assert type_label("donde_esta_mi_pedido") == "Dónde está mi pedido"


def test_no_traduce_lo_que_escribio_el_cliente() -> None:
    markup = (
        '<div class="msg customer"><div class="who">Cliente · 14:03</div>Casos</div>'
        '<p>Casos</p><textarea>Casos</textarea><p title="Aprobar">Gracias, 12.345</p>'
    )
    done = english.translate_html(markup)
    assert done == (
        '<div class="msg customer"><div class="who">Customer · 14:03</div>Casos</div>'
        '<p>Cases</p><textarea>Casos</textarea><p title="Approve">Gracias, 12.345</p>'
    )
