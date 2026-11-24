"""Hito 8, tarea 6 — test de aceptación de la vista "detrás de escena" (decisión 041).

Una página de demo con el sitio de Northwind y el widget a la izquierda y, a la derecha, un panel
en vivo con cada paso del caso: intención, verificación, herramientas con sus parámetros, reglas
del guardián, fuentes citadas, aprobación pendiente, latencia y costo.

- Solo lectura: la página no tiene formularios y el panel solo consulta (GET).
- Sin datos personales sin redactar: ni el email, ni el código, ni el nombre, el teléfono o la
  dirección del cliente.
- Solo muestra la conversación de la sesión del navegador, nunca la de otro.
- Activable por empresa y apagada por defecto.
"""

import json
import re
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack
from test_northwind import _without_the_demo_northwind

from app.channels.web.routes import WebDeps, web_deps
from app.demo import behind, northwind
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.main import app
from app.panel.routes import PanelDeps, panel_deps

SLUG = "northwind-outfitters"
PASSWORD = "clave-de-northwind-demo"


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn, company="northwind")


@pytest.fixture
def web(conn: psycopg.Connection, stack: SimulatedStack) -> Iterator[TestClient]:
    import httpx
    from simulated.voyage import voyage_transport

    embedder = VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )
    _without_the_demo_northwind(conn)
    setup = northwind.ensure_northwind(conn, embedder, password=PASSWORD)
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent
    )
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=setup.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        kick_worker=lambda: None,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _chat(web: TestClient, session: str, text: str) -> str:
    response = web.post("/chat/messages", json={"session": session, "text": text})
    assert response.status_code == 200, response.text
    return response.text


def _conversation(web: TestClient, stack: SimulatedStack) -> str:
    """El comienzo del guion de la demo, por el chat web: verificación, pedido atrasado y un
    reembolso de más de 100 USD."""
    session: str = web.post(f"/chat/session?canal={SLUG}").json()["session"]
    _chat(web, session, "Where is my order NW-4521?")
    _chat(web, session, "My email is emily@example.com")
    _chat(web, session, stack.last_code())
    _chat(web, session, "Thanks. Also, I'd like a refund for order NW-4528.")
    _chat(web, session, "No thanks, I'd just like the refund.")  # retención (decisión 062)
    return session


def _live(web: TestClient, session: str) -> dict[str, Any]:
    response = web.get(f"/demo/{SLUG}/live", params={"session": session})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


# -- 1. la página ---------------------------------------------------------------------------
def test_la_pagina_tiene_el_sitio_el_widget_y_el_panel(web: TestClient) -> None:
    page = web.get(f"/demo/{SLUG}")
    assert page.status_code == 200
    html = page.text
    assert '<html lang="en">' in html and "Northwind Outfitters" in html
    assert f'data-chat="{SLUG}"' in html and "/widget.js" in html  # el widget de siempre
    assert "Trail Runner Jacket" in html  # el catálogo de la tienda
    assert 'id="behind"' in html  # el panel en vivo
    assert "<form" not in html  # solo lectura


# -- 2. cada paso del caso ------------------------------------------------------------------
def test_el_panel_muestra_cada_paso_del_caso(web: TestClient, stack: SimulatedStack) -> None:
    session = _conversation(web, stack)
    data = _live(web, session)
    turns = data["turns"]
    assert len(turns) == 5
    order, retained, refund = turns[2], turns[3], turns[4]
    offered = next(t for t in retained["tools"] if t["name"] == "iniciar_reembolso")
    assert offered["status"] == "denied" and offered["alternatives"]  # retención (decisión 062)

    assert turns[0]["intent"]["name"] == "where_is_my_order"
    assert any(step["kind"] == "code_sent" for step in turns[1]["verification"])
    assert order["verified"] is True

    lookup = order["tools"][0]
    assert (lookup["name"], lookup["params"], lookup["status"]) == (
        "consultar_pedido",
        {"order_id": "NW-4521"},
        "executed",
    )
    rules = {rule["rule"]: rule["ok"] for rule in lookup["rules"]}
    assert rules["verified_customer"] is True and rules["owner"] is True

    started = next(t for t in refund["tools"] if t["name"] == "iniciar_reembolso")
    assert started["status"] == "approval_required"
    assert started["params"] == {"order_id": "NW-4528", "amount": 213}
    approval = {rule["rule"]: rule for rule in started["rules"]}["approval_over"]
    assert approval["ok"] is False and "100" in approval["label"]
    assert refund["approval"] == {"status": "pending"}
    assert refund["action"] == "pedir_aprobacion"

    for turn in (order, refund):
        assert turn["latency_ms"] > 0 and turn["cost_usd"] >= 0 and turn["tokens"] > 0
    assert data["case"]["status"] == "waiting_approval"


def test_el_panel_muestra_en_que_se_fue_el_tiempo(web: TestClient, stack: SimulatedStack) -> None:
    """Decisión 044: la latencia de cada mensaje, repartida en base, modelo (y la espera por el
    límite de Groq), búsqueda, herramientas, verificaciones y email."""
    session = _conversation(web, stack)
    order = _live(web, session)["turns"][2]
    timing = order["timing"]
    for key in ("base", "modelo", "espera", "busqueda", "herramientas", "verificaciones", "email"):
        assert isinstance(timing[f"{key}_ms"], int) and timing[f"{key}_ms"] >= 0, key
    assert timing["queries"] > 0
    # Cada llamada al modelo, con su papel y su duración.
    calls = timing["model_calls"]
    assert [c["name"] for c in calls] == ["classifier", "agent", "agent", "supervisor"]
    assert all(isinstance(c["ms"], int) and isinstance(c["wait_ms"], int) for c in calls)
    page = web.get(f"/demo/{SLUG}").text
    assert "Database" in page and "Waiting for Groq" in page


def test_las_fuentes_citadas(web: TestClient) -> None:
    session = web.post(f"/chat/session?canal={SLUG}").json()["session"]
    _chat(web, session, "What's your return policy?")
    turn = _live(web, session)["turns"][0]
    assert "returns_and_refunds" in turn["sources"]
    assert turn["intent"]["name"] == "consulta_general"


# -- 3. sin datos personales ----------------------------------------------------------------
def test_sin_datos_personales_sin_redactar(web: TestClient, stack: SimulatedStack) -> None:
    session = _conversation(web, stack)
    _chat(web, session, "Please send it to 500 Pine St, Seattle, WA 98101, my phone is 555 0101")
    text = json.dumps(_live(web, session), ensure_ascii=False)
    for personal in ("emily@example.com", "Emily", "Carter", "Alder", "+15555550101", "500 Pine"):
        assert personal not in text, personal
    assert stack.last_code() not in text
    # El texto del cliente no va al panel (puede traer cualquier dato): se ve en el chat.
    assert "Please send it to" not in text and "Thanks. Also" not in text


def test_un_parametro_personal_se_redacta() -> None:
    redacted = behind.redact_params({"order_id": "NW-4545", "address": "500 Pine St, Seattle"})
    assert redacted == {"order_id": "NW-4545", "address": "•••"}
    assert behind.redact_text("I'm emily@example.com, code 614376, +1 555 555 0101") == (
        "I'm [email], code ••••••, [phone]"
    )


# -- 4. solo la sesión propia, solo lectura -------------------------------------------------
def test_solo_la_conversacion_de_la_sesion(web: TestClient, stack: SimulatedStack) -> None:
    mine = _conversation(web, stack)
    other = web.post(f"/chat/session?canal={SLUG}").json()["session"]
    assert _live(web, other)["turns"] == []
    assert len(_live(web, mine)["turns"]) == 5
    assert web.get(f"/demo/{SLUG}/live", params={"session": "x" * 40}).status_code == 401
    assert web.get(f"/demo/{SLUG}/live").status_code == 401
    assert web.post(f"/demo/{SLUG}/live", params={"session": mine}).status_code == 405


# -- 5. activable por empresa, apagada por defecto ------------------------------------------
def test_apagada_por_defecto(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "insert into tenants (name, settings) values ('Otra', '{}') returning id"
    ).fetchone()
    assert row is not None and behind.enabled(conn, row[0]) is False


def test_se_activa_y_desactiva_desde_el_panel(web: TestClient, stack: SimulatedStack) -> None:
    session = _conversation(web, stack)
    login = web.post(
        "/panel/login",
        data={"email": "demo@northwind.example", "password": PASSWORD},
        follow_redirects=False,
    )
    assert login.status_code == 303
    settings = web.get("/panel/operator/behind").text
    assert "Behind the scenes" in settings and f"/demo/{SLUG}" in settings
    token = re.search(r'name="csrf" value="([^"]+)"', settings)
    assert token is not None
    off = web.post(
        "/panel/configuracion/detras-de-escena", data={"activo": "no", "csrf": token.group(1)}
    )
    assert "Behind-the-scenes view turned off." in off.text
    assert web.get(f"/demo/{SLUG}").status_code == 404
    assert web.get(f"/demo/{SLUG}/live", params={"session": session}).status_code == 404
    web.post("/panel/configuracion/detras-de-escena", data={"activo": "si", "csrf": token.group(1)})
    assert web.get(f"/demo/{SLUG}").status_code == 200
