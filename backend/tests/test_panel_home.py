"""Hito 13, tarea 3 — test de aceptación: Home, lo primero que ve el cliente (decisión 085).

- Primeros pasos hasta salir en vivo, empezando por cargar el conocimiento.
- "Needs your attention": aprobaciones, derivaciones y borradores pendientes, cada uno con un
  clic para resolverlo.
- Los números de la semana: conversaciones, resueltas por el agente, derivadas, ventas
  atribuidas y costo.
- El estado del agente (live, paused, setup) y de cada canal.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import build_simulated_stack
from test_panel_ingles import _harbor_data

from app.cases import supervision
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

OWNER, PASSWORD = "maya@harborsupply.example", "clave-de-harbor-supply"
NEW, NEW_PASSWORD = "sol@tiendanueva.example", "clave-de-tienda-nueva"


@dataclass
class World:
    conn: psycopg.Connection
    tenant: uuid.UUID
    case_id: uuid.UUID
    empty: uuid.UUID


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Harbor Supply", OWNER, PASSWORD)
    case_id = _harbor_data(conn, stack, company.tenant_id)
    empty = create_company(conn, "Tienda Nueva", NEW, NEW_PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield World(conn, company.tenant_id, case_id, empty.tenant_id)
    finally:
        app.dependency_overrides.clear()


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/panel/login", data={"email": email, "password": password}).is_success
    return client


def section(page: str, name: str) -> str:
    found = re.search(rf'<section[^>]*aria-labelledby="{name}".*?</section>', page, re.S)
    assert found is not None, name
    return found.group(0)


def test_una_empresa_nueva_empieza_por_el_conocimiento(world: World) -> None:
    page = login(NEW, NEW_PASSWORD).get("/panel/home").text
    steps = section(page, "get-started")
    links = re.findall(r'<a href="(/panel/[^"#]+)[^"]*"', steps)
    assert links[0] == "/panel/knowledge"
    assert "Add your knowledge" in steps and "0 of 6 done" in steps
    status = section(page, "status")
    assert "Setting up" in status
    for channel, state in (
        ("Website chat", "Not installed"),
        ("WhatsApp", "Not connected"),
        ("Store", "Not connected"),
    ):
        assert re.search(rf"{channel}</b>.*?{state}", status, re.S), channel
    attention = section(page, "attention")
    assert "You're all caught up" in attention


def test_lo_pendiente_se_resuelve_con_un_clic(world: World) -> None:
    supervision.set_enabled(world.conn, world.tenant, True)
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "insert into reply_drafts (tenant_id, case_id, channel, text)"
            " values (%s, %s, 'web', 'Your order ships tomorrow.')",
            (world.tenant, world.case_id),
        )
        approval = world.conn.execute("select id from approvals").fetchone()
    assert approval is not None
    client = login(OWNER, PASSWORD)
    page = client.get("/panel/home").text
    attention = section(page, "attention")
    # La aprobación, con sus dos botones, que vuelven a Home.
    assert "Refund · Order NW-4521 · $189.50" in attention
    assert f'action="/panel/aprobaciones/{approval[0]}"' in attention
    assert 'name="volver" value="/panel/home"' in attention
    assert 'value="aprobar"' in attention and 'value="rechazar"' in attention
    # La derivación y el borrador llevan a su caso.
    assert "Handed off" in attention and f'href="/panel/casos/{world.case_id}"' in attention
    assert "Draft to review" in attention
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    done = client.post(
        f"/panel/aprobaciones/{approval[0]}",
        data={"decision": "rechazar", "csrf": token.group(1), "volver": "/panel/home"},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/panel/home?aviso=rechazado"
    assert "Refund · Order NW-4521" not in section(client.get("/panel/home").text, "attention")


def test_los_numeros_de_la_semana(world: World) -> None:
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "insert into usage (tenant_id, kind, model, input_tokens, output_tokens, cost_usd)"
            " values (%s, 'llm', 'claude-sonnet-5-5', 1000, 200, 0.42)",
            (world.tenant,),
        )
        world.conn.execute(
            "insert into checkout_links (tenant_id, case_id, url, currency, amount, completed_at,"
            " ref) values (%s, %s, 'https://x.example/c', 'USD', %s, now(), 'ref-home')",
            (world.tenant, world.case_id, Decimal("120")),
        )
    week = section(login(OWNER, PASSWORD).get("/panel/home").text, "week")
    pairs = re.findall(r'<div class="n">([^<]+)</div><div class="muted">([^<]+)<', week)
    found = {label: value for value, label in pairs}
    assert found["Conversations"] == "1"
    assert found["Resolved by your agent"] == "0"
    assert found["Handed off"] == "1"
    assert found["Attributed sales"] == "$120"
    assert found["AI cost"] == "$0.42"


def test_el_estado_del_agente_en_vivo_y_en_pausa(world: World) -> None:
    client = login(OWNER, PASSWORD)
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"live": True}), world.tenant),
        )
    status = section(client.get("/panel/home").text, "status")
    assert ">Live<" in status and 'value="no"' in status  # se puede pausar desde acá
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"live": False}), world.tenant),
        )
    assert ">Paused<" in section(client.get("/panel/home").text, "status")
