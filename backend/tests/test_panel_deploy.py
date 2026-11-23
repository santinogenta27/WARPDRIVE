"""Hito 13, tarea 4 — test de aceptación: Deploy, una sola pantalla guiada (decisión 086).

- Cinco pasos, en orden, con su estado: tienda, acciones, sitio, WhatsApp y salir en vivo.
- Conectar Shopify prueba la conexión en el momento. Si falla, dice qué falta (un permiso, una
  credencial o la tienda) y qué hacer. Si pasa, los dominios de la tienda se agregan solos a los
  permitidos del chat.
- El paso del sitio trae el código, las guías de Shopify, WordPress, Tiendanube y cualquier sitio
  (ninguna vacía) y "Check installation", que confirma que el chat cargó desde su dominio.
- WhatsApp con su guía para alguien no técnico, y salir en vivo con pausa.
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
from shopify_admin import FakeShopify

from app.channels.web import widget
from app.channels.web.routes import WebDeps, web_deps
from app.companies import deploy
from app.companies.connection import runtime_for
from app.companies.create import create_company
from app.connectors import platforms
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import Guardian

OWNER, PASSWORD = "duena@tiendasur.example", "clave-sur-123"
STEPS = [
    ("store", "Connect your store", "Not connected"),
    ("actions", "Choose what your agent can do", "Connect your store first"),
    ("website", "Add the chat to your website", "Not installed"),
    ("whatsapp", "Connect WhatsApp", "Optional"),
    ("live", "Go live", "Not ready"),
]


@dataclass
class World:
    conn: psycopg.Connection
    tenant: uuid.UUID
    slug: str
    store: FakeShopify
    client: TestClient


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")
    auth._failures.clear()
    made = create_company(conn, "Tienda Sur", OWNER, PASSWORD)
    store = FakeShopify()
    monkeypatch.setattr(platforms, "UPSTREAM", store.transport)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=made.tenant_id,
        guardian=lambda c, t: Guardian(c, runtime_for(c, t)[0]),
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: None
    )
    client = TestClient(app, base_url="https://app.example")
    assert client.post("/panel/login", data={"email": OWNER, "password": PASSWORD}).is_success
    try:
        yield World(conn, made.tenant_id, made.chat_slug, store, client)
    finally:
        app.dependency_overrides.clear()


def csrf(page: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


def step(page: str, key: str) -> str:
    found = re.search(rf'<section class="card dstep[^"]*" id="{key}".*?</section>', page, re.S)
    assert found is not None, key
    return found.group(0)


def connect(world: World, secret: str = "shpss_secreto") -> str:
    page = world.client.get("/panel/deploy").text
    done = world.client.post(
        "/panel/configuracion/sistema/plataforma",
        data={
            "csrf": csrf(page),
            "plataforma": "shopify",
            "metodo": "app",
            "tienda": "mitienda.myshopify.com",
            "client_id": "cid_123",
            "client_secret": secret,
        },
    )
    assert str(done.url).startswith("https://app.example/panel/deploy")
    return done.text


def test_cinco_pasos_en_orden_con_su_estado(world: World) -> None:
    page = world.client.get("/panel/deploy").text
    keys = re.findall(r'<section class="card dstep[^"]*" id="(\w+)"', page)
    assert keys == [key for key, _, _ in STEPS]
    for key, title, state in STEPS:
        section = step(page, key)
        assert title in section and f">{state}<" in section, key
    # El paso de la tienda ofrece las tres opciones.
    store = step(page, "store")
    for option in ("Shopify", "Tiendanube", "Other platform (API)"):
        assert f"{option}\n<small>" in store or f"{option}<small>" in store or option in store


def test_conectar_shopify_prueba_en_el_momento_y_suma_los_dominios(world: World) -> None:
    page = connect(world)
    assert "Store connected. We added mitienda.myshopify.com, www.mitienda.com.ar" in page
    store = step(page, "store")
    assert "<b>Connected to Mi Tienda.</b>" in store
    assert ">Test mode<" in store and "Use my real store" in store
    domains = widget.load(world.conn, world.tenant).domains
    assert "mitienda.myshopify.com" in domains and "www.mitienda.com.ar" in domains
    website = step(page, "website")
    assert "www.mitienda.com.ar" in website  # ya está en los dominios permitidos


def test_la_prueba_dice_que_permiso_falta_y_que_hacer(world: World) -> None:
    world.store.scopes = ["read_orders", "read_products"]
    store = step(connect(world), "store")
    assert "The connection test failed." in store
    assert "What's missing" in store and "A permission" in store
    assert "write_discounts" in store and "Versions" in store and "Release" in store
    assert widget.load(world.conn, world.tenant).domains == ()  # no pasó: no se suma nada


def test_un_secreto_equivocado_es_una_credencial(world: World) -> None:
    page = connect(world, secret="otro")
    assert "Shopify didn&#x27;t accept the credentials." in page
    assert "Client secret again" in page


def test_cada_motivo_dice_que_falta() -> None:
    assert deploy.explain({"ok": False, "codigo": "shop_not_permitted"}, "shopify").missing == (
        deploy.STORE
    )
    assert deploy.explain({"ok": False, "codigo": "token_invalid"}, "tiendanube").missing == (
        deploy.CREDENTIAL
    )
    found = deploy.explain({"ok": False, "status": 404, "codigo": "store_not_found"}, "tiendanube")
    assert found is not None and found.missing == deploy.STORE and "store number" in found.todo
    assert deploy.explain({"ok": False, "status": 500}, "api").missing == deploy.SYSTEM


def test_las_guias_de_instalacion_estan_completas(world: World) -> None:
    website = step(world.client.get("/panel/deploy").text, "website")
    assert re.search(r'<pre id="snippet"[^>]*>.*data-chat=', website, re.S)
    for name, must in (
        ("Shopify", "theme.liquid"),
        ("WordPress", "Custom HTML"),
        ("WordPress", "WPCode"),
        ("Tiendanube", "Códigos externos"),
        ("Tiendanube", "Google Tag Manager"),
        ("Any website", "&lt;/body&gt;"),
    ):
        guide = re.search(
            rf"<summary>{name}</summary>\s*<div class=\"inner\">(.*?)</div>", website, re.S
        )
        assert guide is not None and must in guide.group(1), (name, must)
        assert len(re.findall(r"<li>", guide.group(1))) >= 2, name
    whatsapp = step(world.client.get("/panel/deploy").text, "whatsapp")
    for text in ("Create App", "API Setup", "System users", "Generate token", "messages", "Live"):
        assert text in whatsapp, text


def test_check_installation(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def check(page: str) -> str:
        return world.client.post("/panel/deploy/check-install", data={"csrf": csrf(page)}).text

    page = world.client.get("/panel/deploy").text
    assert "Add your website&#x27;s domain under Allowed domains first." in check(page)
    world.client.post(
        "/panel/deploy/domains", data={"csrf": csrf(page), "dominios": "www.tiendasur.example"}
    )
    pages = {"https://www.tiendasur.example/": "<html><body>Hola</body></html>"}
    monkeypatch.setattr(deploy, "_fetch", lambda url: pages[url])
    assert "We couldn&#x27;t find the code on www.tiendasur.example." in check(page)
    pages["https://www.tiendasur.example/"] = (
        f'<body><script src="https://app.example/widget.js" data-chat="{world.slug}" async>'
        "</script></body>"
    )
    assert "We found the code on www.tiendasur.example, but the chat hasn" in check(page)
    # El widget pidió su configuración desde el dominio: está instalado.
    TestClient(app).get(
        f"/chat/widget/config?canal={world.slug}",
        headers={"Origin": "https://www.tiendasur.example"},
    )
    done = check(page)
    assert "Your chat is installed: it loaded from www.tiendasur.example." in done
    assert ">Installed<" in step(done, "website")


def test_salir_en_vivo_y_pausar(world: World) -> None:
    live = step(world.client.get("/panel/deploy").text, "live")
    assert "Go live" in live and "disabled" in live  # sin reporte no se puede salir
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"live": True}), world.tenant),
        )
    live = step(world.client.get("/panel/deploy").text, "live")
    assert ">Live<" in live and 'name="activo" value="no"' in live
