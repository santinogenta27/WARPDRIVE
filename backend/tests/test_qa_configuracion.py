"""Hito QA 2, etapa 1 — matriz configuración → comportamiento (docs/qa-configuracion.md).

Cada opción que el cliente toca en Agent, Knowledge y Deploy se cambia con el mismo pedido HTTP
que manda el navegador, se verifica que el agente cambia (en lo que se arma antes de llamar al
modelo: instrucciones, herramientas, decisiones del guardián, búsqueda o widget) y se vuelve
atrás para ver que el comportamiento vuelve a ser el de antes. Sin modelo real: el simulado.
"""

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import prompt
from app.cases import supervision, team_notify
from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.companies import deploy, knowledge, rules
from app.companies.create import chat_slug, create_company
from app.db.connection import tenant_scope
from app.main import app
from app.onboarding.ingest import Document
from app.panel import auth, english
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import ContactContext

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
OWNER, OWNER_PASSWORD = "dueno@tiendaqa.example", "clave-de-tienda-qa"
SHOP = "https://www.tiendaqa.example"
OTHER = "https://www.otro-sitio.example"


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    client: TestClient
    tenant: uuid.UUID
    delivered: list[tuple[str, str]] = field(default_factory=list)

    def csrf(self, path: str = "/panel/agent") -> str:
        found = re.search(r'name="csrf" value="([^"]+)"', self.client.get(path).text)
        assert found is not None, path
        return found.group(1)

    def post(self, path: str, data: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        response = self.client.post(
            path, data={"csrf": self.csrf(), **(data or {})}, follow_redirects=False, **kwargs
        )
        assert response.status_code in (200, 303), (path, response.status_code, response.text)
        location = response.headers.get("location", "")
        assert "error=" not in location, unquote(location)
        return response

    def last_agent_request(self) -> dict[str, Any]:
        found = self.stack.agent_requests
        assert found, "el agente no llamó al modelo"
        return found[-1]["body"]

    def system(self) -> str:
        system = self.last_agent_request()["system"]
        if isinstance(system, list):
            return "\n".join(b.get("text", "") for b in system)
        return str(system)

    def tool_names(self) -> list[str]:
        return [t["name"] for t in self.last_agent_request().get("tools", [])]

    def why(self) -> list[Any]:
        """Para los mensajes de error: los últimos pasos del caso."""
        with tenant_scope(self.conn, self.tenant):
            rows = self.conn.execute(
                "select kind, payload from audit_events order by seq desc limit 6"
            ).fetchall()
        return [(k, str(p)[:300]) for k, p in rows]

    def tool_id(self, name: str) -> uuid.UUID:
        with tenant_scope(self.conn, self.tenant):
            row = self.conn.execute("select id from tools where name = %s", (name,)).fetchone()
        assert row is not None, name
        return uuid.UUID(str(row[0]))


def _deps(world_conn: psycopg.Connection, stack: SimulatedStack, tenant: uuid.UUID,
          delivered: list[tuple[str, str]]) -> PanelDeps:  # fmt: skip
    def deliver(c: Any, t: Any, case: Any, channel: str, text: str) -> None:
        delivered.append((channel, text))

    return PanelDeps(
        connection=lambda: nullcontext(world_conn),
        tenant_id=tenant,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
        deliver=deliver,
    )


@pytest.fixture
def acme(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Acme, con el operador en su panel."""
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    # Su propio chat web: el de la base ("web:acme-demo") es de otra empresa.
    address = f"web:qa-acme-{uuid.uuid4().hex[:8]}"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    delivered: list[tuple[str, str]] = []
    app.dependency_overrides[panel_deps] = lambda: _deps(
        conn, stack, stack.seed.tenant_id, delivered
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent, address=address
    )
    client = TestClient(app)
    assert client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD}).is_success
    try:
        yield World(conn, stack, client, stack.seed.tenant_id, delivered)
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


@pytest.fixture
def shop(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """Una empresa nueva (no de demo), con su dueño en el panel y el chat de su sitio."""
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Tienda QA", OWNER, OWNER_PASSWORD)
    delivered: list[tuple[str, str]] = []
    app.dependency_overrides[panel_deps] = lambda: _deps(
        conn, stack, stack.seed.tenant_id, delivered
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent
    )
    client = TestClient(app)
    assert client.post("/panel/login", data={"email": OWNER, "password": OWNER_PASSWORD}).is_success
    try:
        yield World(conn, stack, client, company.tenant_id, delivered)
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


# -- chat del sitio ------------------------------------------------------------------------------
def _events(response: Any) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        found.append((fields["event"], json.loads(fields["data"])))
    return found


def chat(world: World, text: str, origin: str | None = None) -> tuple[str, list[str]]:
    """Abre una sesión del chat de la empresa (desde `origin` o su página propia) y manda un
    mensaje. Devuelve el texto que vio el cliente y los nombres de los eventos."""
    slug = chat_slug(world.conn, world.tenant)
    headers = {"Origin": origin} if origin else {}
    api = TestClient(app)
    session = api.post("/chat/session", params={"canal": slug}, headers=headers)
    assert session.status_code == 201, session.text
    answer = api.post(
        "/chat/messages", json={"session": session.json()["session"], "text": text}, headers=headers
    )
    assert answer.status_code == 200, answer.text
    events = _events(answer)
    said = " ".join(d.get("text", "") for name, d in events if name == "message")
    return said, [name for name, _ in events]


def upload(world: World, name: str, data: bytes, **extra: str) -> None:
    response = world.client.post(
        "/panel/knowledge/upload",
        data={"csrf": world.csrf("/panel/knowledge"), **extra},
        files=[("archivos", (name, data, "application/octet-stream"))],
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["status"] == "ready", response.text


def doc_id(world: World, title: str) -> uuid.UUID | None:
    return next(
        (d.id for d in knowledge.documents(world.conn, world.tenant) if d.title == title), None
    )


# ================================================================================================
# Agent
# ================================================================================================
def test_idioma(acme: World) -> None:
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert prompt.TONES["rioplatense"] in acme.system()
    acme.post("/panel/configuracion/idioma", {"idioma": "en"})
    acme.stack.send("lucia", "Are you open on Sundays?")
    assert prompt.TONES["english"] in acme.system()
    assert prompt.TONES["rioplatense"] not in acme.system()
    acme.post("/panel/configuracion/idioma", {"idioma": "es"})
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert prompt.TONES["rioplatense"] in acme.system()


def _widget(world: World, assistant: str, greeting: str) -> None:
    world.post(
        "/panel/configuracion/widget",
        {
            "volver": "agent/voice",
            "nombre": "Acme Store",
            "color": "#1e3a5f",
            "asistente": assistant,
            "saludo": greeting,
            "sugerencias": "",
        },  # fmt: skip
        files=[("logo", ("", b"", "application/octet-stream"))],
    )


def test_nombre_y_saludo(acme: World) -> None:
    slug = chat_slug(acme.conn, acme.tenant)
    config = TestClient(app).get("/chat/widget/config", params={"canal": slug}).json()
    default_greeting = config["greeting"]
    _widget(acme, "Luna", "¡Hola! Soy Luna, de Acme. ¿En qué te ayudo?")
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert "Te llamás Luna: presentate con ese nombre." in acme.system()
    config = TestClient(app).get("/chat/widget/config", params={"canal": slug}).json()
    assert config["greeting"] == "¡Hola! Soy Luna, de Acme. ¿En qué te ayudo?"
    _widget(acme, "", "")
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert "Te llamás" not in acme.system()
    config = TestClient(app).get("/chat/widget/config", params={"canal": slug}).json()
    assert config["greeting"] == default_greeting


def _voice(world: World, **values: str) -> None:
    data = {"tono": "", "trato": "", "usar": "", "evitar": "", "emojis": "si", "firma": ""}
    world.post("/panel/configuracion/voz", {**data, **values})


@pytest.mark.parametrize(("trato", "tone"), [("usted", "usted"), ("tu", "neutro")])
def test_tono_y_trato(acme: World, trato: str, tone: str) -> None:
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert prompt.TONES["rioplatense"] in acme.system()
    _voice(acme, trato=trato, tono="cálido y breve")
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert prompt.TONES[tone] in acme.system()
    assert "Tono de la marca: cálido y breve." in acme.system()
    _voice(acme)
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert prompt.TONES["rioplatense"] in acme.system()
    assert "<voz_de_marca>" not in acme.system()


def test_palabras_a_usar_y_a_evitar(acme: World) -> None:
    _voice(acme, usar="genial, con gusto", evitar="despachado")
    result = acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert 'Usá estas palabras cuando encajen: "genial", "con gusto".' in acme.system()
    assert 'Nunca uses estas palabras: "despachado".' in acme.system()
    # El simulado contesta "está despachado": el supervisor lo frena en código.
    assert "despachado" not in result.message.lower()
    _voice(acme)
    result = acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert "<voz_de_marca>" not in acme.system()
    assert "despachado" in result.message.lower()


def test_emojis(acme: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent import loop

    original = loop.voice.apply
    monkeypatch.setattr(loop.voice, "apply", lambda text, v: original(text + " 🙂", v))
    with_emojis = acme.stack.send("lucia", "¿Abren los domingos?")
    assert "🙂" in with_emojis.message
    _voice(acme, emojis="no")
    without = acme.stack.send("lucia", "¿Abren los domingos?")
    assert "Nunca uses emojis." in acme.system() and "🙂" not in without.message
    _voice(acme, emojis="si")
    again = acme.stack.send("lucia", "¿Abren los domingos?")
    assert "🙂" in again.message


def test_firma(acme: World) -> None:
    _voice(acme, firma="— Equipo Acme")
    signed = acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert signed.message.rstrip().endswith("— Equipo Acme")
    _voice(acme)
    plain = acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert "— Equipo Acme" not in plain.message


def _tool(world: World, name: str, **values: str) -> None:
    world.post(f"/panel/configuracion/herramientas/{world.tool_id(name)}", values)


def _refund(world: World) -> Any:
    world.stack.close_open_cases()
    with tenant_scope(world.conn, world.tenant):  # una pendiente frena la siguiente
        world.conn.execute("update approvals set status = 'rejected' where status = 'pending'")
    result = world.stack.send("martin", "Quiero el reembolso del pedido 1005")
    return next(t for t in result.tool_calls if t.name == "iniciar_reembolso")


def test_regla_de_monto_maximo(acme: World) -> None:
    first = _refund(acme)
    assert first.status == "approval_required", (first, acme.why())
    _tool(acme, "iniciar_reembolso", limites="si", monto_maximo="1000", aprobacion_sobre="0")
    refused = _refund(acme)
    assert refused.status == "denied", (refused, acme.why())
    _tool(acme, "iniciar_reembolso", limites="si", monto_maximo="", aprobacion_sobre="0")
    assert _refund(acme).status == "approval_required"


def _change_address(world: World) -> Any:
    world.stack.close_open_cases()
    result = world.stack.send(
        "lucia", "Quiero cambiar la dirección del pedido 1002 a Av. Corrientes 1234, CABA"
    )
    return next(t for t in result.tool_calls if t.name == "actualizar_direccion")


def test_que_requiere_aprobacion(acme: World) -> None:
    before = _change_address(acme).status
    assert before == "executed", before
    _tool(acme, "actualizar_direccion", reglas="si", siempre="si", identidad="si")
    assert _change_address(acme).status == "approval_required"
    _tool(acme, "actualizar_direccion", reglas="si", identidad="si")
    acme.stack.close_open_cases()
    with tenant_scope(acme.conn, acme.tenant):  # la aprobación pendiente no frena la siguiente
        acme.conn.execute("update approvals set status = 'rejected' where status = 'pending'")
    assert _change_address(acme).status == "executed"


def test_verificacion_de_identidad_obligatoria(acme: World) -> None:
    acme.stack.add_contact("anonimo")
    first = acme.stack.send("anonimo", "¿Dónde está mi pedido 1001?")
    assert first.verification_requested
    assert all(t.status == "denied" for t in first.tool_calls if t.name == "consultar_pedido")
    _tool(acme, "consultar_pedido", reglas="si", identidad="")
    acme.stack.close_open_cases()
    acme.stack.add_contact("anonimo2")
    open_ = acme.stack.send("anonimo2", "¿Dónde está mi pedido 1001?")
    # Sin exigir identidad no se pide el código; el dueño del pedido lo sigue controlando el
    # guardián en código, así que un pedido ajeno no se muestra igual.
    assert not open_.verification_requested, (open_.tool_calls, acme.why())
    lookups = [t for t in open_.tool_calls if t.name == "consultar_pedido"]
    assert lookups and all(t.reason == "El recurso no pertenece al contacto." for t in lookups)
    _tool(acme, "consultar_pedido", reglas="si", identidad="si")
    acme.stack.close_open_cases()
    acme.stack.add_contact("anonimo3")
    again = acme.stack.send("anonimo3", "¿Dónde está mi pedido 1001?")
    assert again.verification_requested


def test_horario_de_atencion_y_a_quien_derivar(
    acme: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    sunday_night = datetime(2026, 11, 22, 23, 0, tzinfo=rules.ZONE)
    monkeypatch.setattr(rules, "_now", lambda: sunday_night)
    sent: list[Any] = []
    monkeypatch.setattr(team_notify, "_mailer", lambda: type("M", (), {"send": sent.append})())
    open_hours = acme.stack.send("lucia", "Quiero hablar con una persona")
    assert "Nuestro equipo atiende" not in open_hours.message
    days = {f"dia_{d}": "si" for d in range(1, 6)}
    hours = {"desde": "09:00", "hasta": "18:00", "derivar_a": "guardia@acme.example"}
    acme.post("/panel/configuracion/reglas", {**days, **hours})
    acme.stack.close_open_cases()
    closed = acme.stack.send("lucia", "Quiero hablar con una persona")
    assert closed.action == "derivar"
    assert "Nuestro equipo atiende de lunes a viernes de 09:00 a 18:00" in closed.message
    assert team_notify.recipients(acme.conn, acme.tenant) == ["guardia@acme.example"]
    assert any("guardia@acme.example" in str(getattr(e, "to", "")) for e in sent), sent
    acme.post("/panel/configuracion/reglas", {"desde": "", "hasta": "", "derivar_a": ""})
    acme.stack.close_open_cases()
    back = acme.stack.send("lucia", "Quiero hablar con una persona")
    assert "Nuestro equipo atiende" not in back.message
    assert team_notify.recipients(acme.conn, acme.tenant) != ["guardia@acme.example"]


def test_ventas_productos_a_ofrecer_y_descuento_maximo(acme: World) -> None:
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert "ofrecer_producto" not in acme.tool_names()
    acme.post("/panel/configuracion/ventas",
              {"producto": "Medias de running", "descuento": "10",
               "tipo_horarios_y_sucursales": "si"})  # fmt: skip
    acme.stack.close_open_cases()
    acme.stack.send("lucia", "¿Abren los domingos?")
    offer = next(t for t in acme.last_agent_request()["tools"] if t["name"] == "ofrecer_producto")
    assert "Medias de running (hasta 10 % de descuento)" in offer["description"]
    with tenant_scope(acme.conn, acme.tenant):
        rule = acme.conn.execute("select id from upsell_rules").fetchone()
    assert rule is not None
    acme.post(f"/panel/configuracion/ventas/{rule[0]}/borrar")
    acme.stack.close_open_cases()
    acme.stack.send("lucia", "¿Abren los domingos?")
    assert "ofrecer_producto" not in acme.tool_names()


def _supervisor_calls(world: World) -> int:
    return sum(1 for r in world.stack.claude_requests if "supervisar" in r["tool_names"])


def test_supervisor_encendido_y_apagado(acme: World) -> None:
    acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    on = _supervisor_calls(acme)
    assert on >= 1
    acme.post("/panel/configuracion/supervisor", {"activo": "no"})
    acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert _supervisor_calls(acme) == on
    acme.post("/panel/configuracion/supervisor", {"activo": "si"})
    acme.stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert _supervisor_calls(acme) > on


def test_modo_supervisado(acme: World) -> None:
    said, _ = chat(acme, "¿Abren los domingos?")
    assert said and not supervision.pending(acme.conn, acme.tenant)
    acme.post("/panel/configuracion/supervisado", {"activo": "si"})
    held, _ = chat(acme, "¿Abren los domingos?")
    assert not held
    assert len(supervision.pending(acme.conn, acme.tenant)) == 1
    acme.post("/panel/configuracion/supervisado", {"activo": "no"})
    said, _ = chat(acme, "¿Abren los domingos?")
    assert said and len(supervision.pending(acme.conn, acme.tenant)) == 1


# ================================================================================================
# Knowledge
# ================================================================================================
POLICY_V1 = b"# Envios al sur\n\nEl envio a Ushuaia tarda 18 dias habiles desde la compra.\n"
POLICY_V2 = b"# Envios al sur\n\nEl envio a Ushuaia tarda 24 dias habiles desde la compra.\n"
QUESTION = "¿Cuánto tarda el envio a Ushuaia?"


def test_documento_subir_reemplazar_y_borrar(shop: World) -> None:
    said, _ = chat(shop, QUESTION)
    assert "18 dias" not in said
    upload(shop, "garantia.md", POLICY_V1)
    said, _ = chat(shop, QUESTION)
    assert "18 dias" in said, (said, shop.why())
    first = doc_id(shop, "Envios al sur")
    assert first is not None
    sources = shop.stack.agent_requests[-1]["body"]
    assert "18 dias" in json.dumps(sources, ensure_ascii=False)
    # Reemplazar: usa la versión nueva.
    upload(shop, "garantia_v2.md", POLICY_V2, reemplazar=str(first))
    said, _ = chat(shop, QUESTION)
    assert "24 dias" in said and "18 dias" not in said, said
    # Borrar: deja de usarlo al instante.
    current = doc_id(shop, "Envios al sur")
    shop.post(f"/panel/onboarding/indexados/{current}/borrar")
    said, _ = chat(shop, QUESTION)
    assert "24 dias" not in said and "18 dias" not in said, said
    assert "dias habiles desde la compra" not in json.dumps(
        shop.last_agent_request(), ensure_ascii=False
    )


def test_el_agente_cita_el_documento(shop: World) -> None:
    upload(shop, "garantia.md", POLICY_V1)
    chat(shop, QUESTION)
    with tenant_scope(shop.conn, shop.tenant):
        row = shop.conn.execute(
            "select payload from audit_events where kind = 'final_action' order by seq desc limit 1"
        ).fetchone()
    assert row is not None, "falta el evento de respuesta"
    assert any("garantia" in str(s).lower() for s in row[0].get("sources", [])), row[0]


PAGES = [
    ("https://www.tiendaqa.example/", "Inicio", "Bienvenidos a Tienda QA."),
    (
        "https://www.tiendaqa.example/envios",
        "Envios",
        "El envio a Tierra del Fuego tarda 11 dias habiles.",
    ),  # fmt: skip
]


def test_paginas_del_sitio(shop: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        knowledge, "_crawl", lambda url, http=None: [
            (u, Document(title=t, source=u, text=x)) for u, t, x in PAGES
        ]
    )  # fmt: skip
    shop.post("/panel/knowledge/site", {"sitio": "https://www.tiendaqa.example"})
    found = knowledge.site(shop.conn, shop.tenant)
    assert found is not None
    pages = {p.title: p for p in knowledge.pages(shop.conn, shop.tenant, found.id)}
    question = "¿Cuánto tarda el envio a Tierra del Fuego?"
    chosen = {p.title for p in pages.values() if p.selected}
    said, _ = chat(shop, question)
    assert ("11 dias" in said) == ("Envios" in chosen), (chosen, said)
    shop.post("/panel/knowledge/site/pages", {"pagina": [str(pages["Inicio"].id)]})
    said, _ = chat(shop, question)
    assert "11 dias" not in said, said
    shop.post(
        "/panel/knowledge/site/pages",
        {"pagina": [str(pages["Inicio"].id), str(pages["Envios"].id)]},
    )
    said, _ = chat(shop, question)
    assert "11 dias" in said, said


def test_ask_your_knowledge_coincide_con_el_chat(shop: World) -> None:
    upload(shop, "garantia.md", POLICY_V1)
    upload(shop, "relojes.md", b"# Garantia\n\nLa garantia de los relojes es de un anio.\n")
    page = shop.post("/panel/knowledge/ask", {"pregunta": QUESTION}).text
    assert "Envios al sur" in page and "18 dias" in page
    said, _ = chat(shop, QUESTION)
    assert "18 dias" in said, (said, shop.why())
    docs = json.dumps(shop.last_agent_request(), ensure_ascii=False)
    assert "18 dias" in docs


# ================================================================================================
# Deploy
# ================================================================================================
def _config(world: World, origin: str) -> dict[str, Any]:
    slug = chat_slug(world.conn, world.tenant)
    data: dict[str, Any] = (
        TestClient(app)
        .get("/chat/widget/config", params={"canal": slug}, headers={"Origin": origin})
        .json()
    )
    return data


def _go_live(world: World) -> None:
    report = {"resolucion_estimada": 0.8, "adversariales": {"total": 4, "superados": 4}}
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "insert into onboarding_runs (tenant_id, status, report) values (%s, 'done', %s)",
            (world.tenant, Jsonb(report)),
        )
    world.post("/panel/salir-en-vivo", {"activo": "si"})


def test_dominios_permitidos(shop: World) -> None:
    _go_live(shop)
    assert _config(shop, SHOP)["allowed"] is False
    shop.post("/panel/deploy/domains", {"dominios": "www.tiendaqa.example"})
    assert _config(shop, SHOP)["allowed"] is True
    assert _config(shop, OTHER)["allowed"] is False
    slug = chat_slug(shop.conn, shop.tenant)
    refused = TestClient(app).post("/chat/session", params={"canal": slug},
                                   headers={"Origin": OTHER})  # fmt: skip
    assert refused.status_code == 403
    shop.post("/panel/deploy/domains", {"dominios": ""})
    assert _config(shop, SHOP)["allowed"] is False


def test_check_installation(shop: World, monkeypatch: pytest.MonkeyPatch) -> None:
    slug = chat_slug(shop.conn, shop.tenant)
    monkeypatch.setattr(deploy, "_fetch", lambda url: "<html><body>Hola</body></html>")
    empty = shop.client.post(
        "/panel/deploy/check-install",
        data={"csrf": shop.csrf()},
        follow_redirects=False,
    )
    assert "Allowed domains first" in unquote(empty.headers["location"])
    shop.post("/panel/deploy/domains", {"dominios": "www.tiendaqa.example"})
    assert deploy.check_install(shop.conn, shop.tenant, slug).status == "missing"
    code = f'<script src="x/widget.js" data-chat="{slug}"></script>'
    monkeypatch.setattr(deploy, "_fetch", lambda url: f"<html><body>{code}</body></html>")
    assert deploy.check_install(shop.conn, shop.tenant, slug).status == "found"
    _go_live(shop)
    assert _config(shop, SHOP)["allowed"] is True
    assert deploy.check_install(shop.conn, shop.tenant, slug).status == "loaded"


def test_pausar_y_reanudar(shop: World) -> None:
    upload(shop, "garantia.md", POLICY_V1)
    shop.post("/panel/deploy/domains", {"dominios": "www.tiendaqa.example"})
    _go_live(shop)
    said, _ = chat(shop, QUESTION, origin=SHOP)
    assert "18 dias" in said, (said, shop.why())
    calls = len(shop.stack.agent_requests)
    shop.post("/panel/salir-en-vivo", {"activo": "no"})
    # En pausa: el chat sigue en el sitio, pero no responde el agente; lo pasa a una persona.
    assert _config(shop, SHOP)["allowed"] is True
    paused, _ = chat(shop, QUESTION, origin=SHOP)
    assert "18 dias" not in paused and paused, paused
    assert len(shop.stack.agent_requests) == calls
    with tenant_scope(shop.conn, shop.tenant):
        status = shop.conn.execute(
            "select c.status from cases c join audit_events a on a.case_id = c.id"
            " where a.kind = 'escalated' and a.payload -> 'causa' ->> 'tipo' = 'agente_en_pausa'"
        ).fetchone()
    assert status is not None and status[0] == "escalated", status
    shop.post("/panel/salir-en-vivo", {"activo": "si"})
    said, _ = chat(shop, QUESTION, origin=SHOP)
    assert "18 dias" in said, (said, shop.why())


def test_acciones_desactivadas_de_la_tienda(acme: World) -> None:
    """Acme usa su tienda con la API simulada: una acción apagada no se ofrece ni se ejecuta."""
    acme.stack.send("lucia", "Quiero cancelar el pedido 1002")
    assert "cancelar_pedido" in acme.tool_names()
    _tool(acme, "cancelar_pedido", aprobada="no")
    acme.stack.close_open_cases()
    result = acme.stack.send("lucia", "Quiero cancelar el pedido 1002")
    assert "cancelar_pedido" not in acme.tool_names()
    assert not any(
        t.name == "cancelar_pedido" and t.status == "executed" for t in result.tool_calls
    )
    # Aunque el modelo la pidiera, el guardián la niega: no está aprobada.
    contact = ContactContext(id=acme.stack.add_contact("x"), phone_hash="x", verified=True)
    decision = acme.stack.agent._guardian.execute(
        tenant_id=acme.tenant, case_id=result.case_id, contact=contact,
        tool_name="cancelar_pedido", tool_input={"order_id": "1002"},
        allowed={"cancelar_pedido"},
    )  # fmt: skip
    assert decision.status == "denied"
    _tool(acme, "cancelar_pedido", aprobada="si")
    acme.stack.close_open_cases()
    acme.stack.send("lucia", "Quiero cancelar el pedido 1002")
    assert "cancelar_pedido" in acme.tool_names()
