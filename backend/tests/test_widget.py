"""Hito 7, tarea 1 — test de aceptación del widget instalable (decisión 031).

Una línea de `<script>` abre el chat de la empresa en el sitio del cliente, con el mismo flujo
que `/c/…`. Cada empresa define desde qué dominios se puede usar y su marca (nombre, color y
logo). La página `/c/…` y la de la demo usan el mismo widget, así se ven iguales.
"""

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.web import widget
from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.companies.create import chat_slug, create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"
SHOP = "https://www.tiendanorte.example"
# Un PNG válido de 1 x 1 píxel.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    acme_slug: str
    norte: uuid.UUID
    norte_slug: str
    api: TestClient


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    acme_slug = f"acme-test-{uuid.uuid4().hex[:8]}"
    register_whatsapp_channel(
        conn, stack.seed.tenant_id, phone_number_id=f"web:{acme_slug}", kind="web"
    )
    company = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn),
        agent=lambda c: stack.agent,
        address=f"web:{acme_slug}",
    )
    try:
        yield World(conn, stack, acme_slug, company.tenant_id, company.chat_slug, TestClient(app))
    finally:
        app.dependency_overrides.clear()


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


def csrf(client: TestClient) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', client.get("/panel/configuracion").text)
    assert match is not None
    return match.group(1)


def save_widget(client: TestClient, files: list | None = None, **fields: str) -> str:
    data = {
        "csrf": csrf(client),
        "nombre": "Tienda Norte",
        "color": "#0f766e",
        "dominios": "",
        "sugerencias": "",
        **fields,
    }
    response = client.post(
        "/panel/configuracion/widget",
        data=data,
        # Sin archivo igual va como multipart, como lo manda el formulario del panel.
        files=files or [("logo", ("", b"", "application/octet-stream"))],
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location: str = response.headers["location"]
    return location


def events(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    found = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        found.append((fields["event"], json.loads(fields["data"])))
    return found


# -- el archivo del widget -------------------------------------------------------------------
def test_el_widget_es_un_solo_archivo_liviano_y_sin_dependencias(world: World) -> None:
    response = world.api.get("/widget.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "max-age" in response.headers["cache-control"]
    script = response.text
    assert len(script.encode()) < 45_000  # chico: sin minificar entra en una sola respuesta
    assert "attachShadow" in script  # aislado del CSS del sitio
    assert "prefers-reduced-motion" in script and "prefers-color-scheme" in script
    assert "backdrop-filter" in script and "safe-area-inset-bottom" in script
    assert 'aria-live="polite"' in script and "aria-expanded" in script
    # No carga nada de otros servidores: ni fuentes, ni librerías, ni imágenes.
    assert not re.search(r"https?://(?!www\.w3\.org/)", script)
    assert "import " not in script and "require(" not in script
    # Sin <form>: en un sitio que envuelve la página en un formulario, el navegador descarta
    # uno anidado y el chat quedaría sin campo de texto.
    assert "<form" not in script
    # Un mensaje del equipo ya mostrado no se repite (dos consultas superpuestas).
    assert "seen.indexOf(m.id)" in script


def test_la_pagina_de_la_empresa_y_la_de_la_demo_usan_el_mismo_widget(world: World) -> None:
    company = world.api.get(f"/c/{world.norte_slug}")
    assert company.status_code == 200
    assert f'data-chat="{world.norte_slug}"' in company.text
    assert 'src="/widget.js"' in company.text and 'data-mode="page"' in company.text
    assert "Tienda Norte" in company.text and "Acme" not in company.text
    assert world.api.get("/c/no-existe-123").status_code == 404

    for path in ("/", "/chat"):
        demo = world.api.get(path)
        assert demo.status_code == 200
        assert f'data-chat="{world.acme_slug}"' in demo.text
        assert 'src="/widget.js"' in demo.text and 'data-mode="page"' in demo.text
        assert "lucia@example.com" in demo.text  # cómo probar la verificación en la demo


# -- configuración pública y marca -----------------------------------------------------------
def test_la_configuracion_publica_trae_la_marca_de_la_empresa(world: World) -> None:
    default = world.api.get("/chat/widget/config", params={"canal": world.norte_slug}).json()
    assert default["allowed"] is True and default["name"] == "Tienda Norte"
    assert re.fullmatch(r"#[0-9a-f]{6}", default["accent"]) and default["logo"] is None
    assert "Tienda Norte" in default["greeting"]

    norte = login(NORTE, NORTE_PASSWORD)
    location = save_widget(
        norte,
        files=[("logo", ("logo.png", PNG, "image/png"))],
        nombre="Norte Deportes",
        color="#0F766E",
        sugerencias="¿Hacen envíos?\n¿Cómo cambio un producto?\n\n",
    )
    assert "ok=" in location
    config = world.api.get("/chat/widget/config", params={"canal": world.norte_slug}).json()
    assert config["name"] == "Norte Deportes" and config["accent"] == "#0f766e"
    assert config["suggestions"] == ["¿Hacen envíos?", "¿Cómo cambio un producto?"]
    assert "Norte Deportes" in config["greeting"]
    logo = world.api.get(config["logo"])
    assert logo.status_code == 200 and logo.content == PNG
    assert logo.headers["content-type"] == "image/png"
    assert logo.headers["x-content-type-options"] == "nosniff"
    # La marca de Acme no cambió.
    acme = world.api.get("/chat/widget/config", params={"canal": world.acme_slug}).json()
    assert acme["name"] == "Acme Store" and acme["logo"] is None
    assert "¿Abren los domingos?" in acme["suggestions"]  # las de la demo, por defecto
    assert default["suggestions"] == []  # una empresa nueva no hereda las de Acme
    assert (
        world.api.get("/chat/widget/config", params={"canal": "no-existe-123"}).status_code == 404
    )

    # Quitar el logo.
    save_widget(norte, quitar_logo="si")
    config = world.api.get("/chat/widget/config", params={"canal": world.norte_slug}).json()
    assert config["logo"] is None


def test_el_panel_valida_la_marca_y_los_dominios(world: World) -> None:
    norte = login(NORTE, NORTE_PASSWORD)
    bad = [
        {"color": "rojo"},
        {"color": "#12345"},
        {"nombre": ""},
        {"nombre": "x" * 61},
        {"dominios": "https://tienda.example/carrito"},
        {"dominios": "tienda norte.example"},
        {"dominios": "*"},
        {"dominios": "*.com"},
        {"sugerencias": "x" * 81},
    ]
    for fields in bad:
        assert "error=" in save_widget(norte, **fields), fields
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert "error=" in save_widget(norte, files=[("logo", ("logo.svg", svg, "image/svg+xml"))])
    assert "error=" in save_widget(
        norte, files=[("logo", ("logo.png", b"no es un png", "image/png"))]
    )
    big = PNG + b"\0" * (widget.MAX_LOGO_BYTES + 1)
    assert "error=" in save_widget(norte, files=[("logo", ("logo.png", big, "image/png"))])
    # Nada de eso se guardó.
    assert widget.load(world.conn, world.norte).accent == widget.DEFAULT_ACCENT

    # Los dominios se guardan limpios: sin protocolo, sin mayúsculas, sin repetidos.
    location = save_widget(
        norte,
        dominios="https://www.TiendaNorte.example\n*.tiendanorte.example, localhost\n"
        "www.tiendanorte.example",
    )
    assert "ok=" in location
    assert widget.load(world.conn, world.norte).domains == (
        "www.tiendanorte.example",
        "*.tiendanorte.example",
        "localhost",
    )


def test_configuracion_muestra_el_codigo_para_copiar_y_la_vista_previa(world: World) -> None:
    norte = login(NORTE, NORTE_PASSWORD)
    save_widget(norte, dominios="www.tiendanorte.example")
    page = norte.get("/panel/configuracion").text
    snippet = (
        f"&lt;script src=&quot;http://testserver/widget.js&quot; "
        f"data-chat=&quot;{world.norte_slug}&quot; async&gt;&lt;/script&gt;"
    )
    assert snippet in page
    assert "www.tiendanorte.example" in page and 'value="#0f766e"' in page
    # Vista previa: el widget real, dentro de la página de Configuración.
    assert 'data-mode="inline"' in page and f'data-chat="{world.norte_slug}"' in page

    # Otra empresa no puede cambiar esta marca: cada una guarda la suya.
    admin = login(ADMIN, ADMIN_PASSWORD)
    response = admin.post(
        "/panel/configuracion/widget",
        data={"csrf": csrf(admin), "nombre": "Otra", "color": "#ff0000", "dominios": ""},
        files=[("logo", ("", b"", "application/octet-stream"))],
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert widget.load(world.conn, world.norte).name == "Tienda Norte"
    assert widget.load(world.conn, world.stack.seed.tenant_id).name == "Otra"
    # Sin token anti-CSRF no se guarda nada.
    forged = norte.post(
        "/panel/configuracion/widget",
        data={"nombre": "Hackeada", "color": "#000000", "dominios": ""},
        files=[("logo", ("", b"", "application/octet-stream"))],
        follow_redirects=False,
    )
    assert forged.status_code == 403
    assert widget.load(world.conn, world.norte).name == "Tienda Norte"


# -- dominios permitidos ---------------------------------------------------------------------
def test_reglas_de_dominios_permitidos() -> None:
    domains = ("www.tiendanorte.example", "*.norte.example", "localhost")
    allowed = widget.origin_allowed
    assert allowed(None, "app.example", domains)  # sin Origin: no es un navegador en otro sitio
    assert allowed("https://app.example", "app.example", ())  # el propio servidor (/c/…)
    assert allowed("https://www.tiendanorte.example", "app.example", domains)
    assert allowed("https://WWW.TiendaNorte.example:8443", "app.example", domains)
    assert allowed("https://tienda.norte.example", "app.example", domains)
    assert allowed("https://a.b.norte.example", "app.example", domains)
    assert allowed("http://localhost:5173", "app.example", domains)
    assert not allowed(
        "https://norte.example", "app.example", domains
    )  # el comodín es de subdominios
    assert not allowed("https://tiendanorte.example", "app.example", domains)
    assert not allowed("https://www.tiendanorte.example.evil.test", "app.example", domains)
    assert not allowed("https://evilnorte.example", "app.example", domains)
    assert not allowed("null", "app.example", domains)
    assert not allowed("https://www.tiendanorte.example", "app.example", ())


def test_el_chat_solo_responde_desde_los_dominios_permitidos(world: World) -> None:
    api, slug = world.api, world.norte_slug
    evil = {"Origin": "https://otro-sitio.example"}
    shop = {"Origin": SHOP}

    # Sin dominios cargados, el widget no funciona en ningún sitio ajeno.
    config = api.get("/chat/widget/config", params={"canal": slug}, headers=shop)
    assert config.json()["allowed"] is False
    assert config.headers["access-control-allow-origin"] == "*"  # el widget lee el motivo
    assert api.post("/chat/session", params={"canal": slug}, headers=shop).status_code == 403

    save_widget(login(NORTE, NORTE_PASSWORD), dominios="www.tiendanorte.example")
    assert api.get("/chat/widget/config", params={"canal": slug}, headers=shop).json()["allowed"]
    assert not api.get("/chat/widget/config", params={"canal": slug}, headers=evil).json()[
        "allowed"
    ]

    # Sesión: desde el sitio del cliente sí, desde otro no; la página propia sigue andando.
    refused = api.post("/chat/session", params={"canal": slug}, headers=evil)
    assert refused.status_code == 403 and "access-control-allow-origin" not in refused.headers
    created = api.post("/chat/session", params={"canal": slug}, headers=shop)
    assert created.status_code == 201
    assert created.headers["access-control-allow-origin"] == SHOP
    assert "origin" in created.headers["vary"].lower()
    session = created.json()["session"]
    assert api.post("/chat/session", params={"canal": slug}).status_code == 201

    # El navegador pregunta antes de mandar JSON a otro servidor (preflight).
    preflight = api.options(
        "/chat/messages",
        headers={
            **shop,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == SHOP
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "content-type" in preflight.headers["access-control-allow-headers"].lower()

    # Mensajes y avisos del equipo: con la sesión de Norte, solo desde su dominio.
    body = {"session": session, "text": "hola"}
    assert api.post("/chat/messages", json=body, headers=evil).status_code == 403
    assert api.get("/chat/updates", params={"session": session}, headers=evil).status_code == 403
    with tenant_scope(world.conn, world.norte):
        assert world.conn.execute("select count(*) from cases").fetchone() == (0,)
    updates = api.get("/chat/updates", params={"session": session}, headers=shop)
    assert updates.status_code == 200 and updates.json() == {"messages": []}
    assert updates.headers["access-control-allow-origin"] == SHOP

    # Un dominio permitido para Norte no sirve para el chat de Acme.
    assert (
        api.post("/chat/session", params={"canal": world.acme_slug}, headers=shop).status_code
        == 403
    )


def test_el_widget_sigue_el_mismo_flujo_que_la_pagina_con_verificacion(world: World) -> None:
    """Desde el sitio del cliente (otro origen): verificación por código y respuesta con la
    herramienta, igual que en la página del chat."""
    api, stack = world.api, world.stack
    widget.save(
        world.conn,
        stack.seed.tenant_id,
        name="Acme Store",
        accent="#1e3a5f",
        domains="www.acmestore.example",
        suggestions="",
    )
    origin = {"Origin": "https://www.acmestore.example"}
    created = api.post("/chat/session", params={"canal": world.acme_slug}, headers=origin)
    session = created.json()["session"]

    def send(text: str) -> str:
        response = api.post(
            "/chat/messages", json={"session": session, "text": text}, headers=origin
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin["Origin"]
        found = events(response)
        assert found[0][0] == "typing" and found[-1][0] == "done"
        return " ".join(data["text"] for name, data in found if name == "message")

    assert "email" in send("¿Dónde está mi pedido 1001?")
    assert "código" in send("lucia@example.com")
    code = re.search(r"\b(\d{6})\b", stack.mailer.sent[-1].body)
    assert code is not None
    answer = send(code.group(1))
    assert "1001" in answer
    assert chat_slug(world.conn, stack.seed.tenant_id) is not None
