"""Hito 10, punto 5 — test de aceptación: ventas atribuidas al agente (decisión 063).

- Cada link de compra lleva el identificador de su conversación (`ref`). Lo pone el guardián, no
  el modelo: el campo no está en la herramienta.
- El link queda anotado. Cuando la tienda informa que esa compra se completó
  (`GET /checkouts/{ref}`), es una venta atribuida al agente, con su pedido y su monto. Volver a
  sincronizar no la duplica.
- En Métricas: "Ventas atribuidas al agente" (cantidad y monto).
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from shopify_admin import FakeShopify, product, variant
from simulated.stack import build_simulated_stack

from app.connectors import shopify
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps
from app.sales import attribution
from app.tools.runtime import ToolRuntime

ACME_EMAIL, ACME_PASSWORD = "operador@plataforma.example", "clave-del-operador"


@pytest.fixture
def northwind(conn: psycopg.Connection) -> Any:
    return build_simulated_stack(conn, company="northwind")


def _buy(stack: Any) -> Any:
    stack.send("sophia", "Do you have a rain jacket in size M?")
    return stack.send("sophia", "Great, I'll take the black one")


def test_el_link_lleva_la_conversacion_y_el_modelo_no_la_elige(northwind: Any) -> None:
    from app.companies import review

    tools = {t.name: t for t in review.tools(northwind.conn, northwind.seed.tenant_id)}
    manifest = tools["armar_carrito"].manifest
    assert manifest["conversation_ref"] == "ref"
    assert "ref" not in manifest["input_schema"]["properties"]

    result = _buy(northwind)
    ref = attribution.conversation_ref(result.case_id)
    url = next(u for u in re.findall(r"https?://\S+", result.message) if "/cart/" in u)
    assert parse_qs(urlsplit(url).query)["ref"] == [ref]
    found = attribution.totals(northwind.conn, northwind.seed.tenant_id)
    assert [(t.currency, t.sales, t.links) for t in found] == [("USD", 0, 1)]


def test_una_compra_completada_es_una_venta_atribuida_una_sola_vez(northwind: Any) -> None:
    tenant = northwind.seed.tenant_id
    result = _buy(northwind)
    guardian = northwind.agent._guardian
    assert attribution.sync(northwind.conn, tenant, guardian) == 0  # todavía no pagó
    ref = attribution.conversation_ref(result.case_id)
    paid = northwind.acme.post(f"/checkouts/{ref}/complete")  # paga en la tienda
    assert paid.json()["status"] == "completed"
    assert attribution.sync(northwind.conn, tenant, guardian) == 1
    assert attribution.sync(northwind.conn, tenant, guardian) == 0  # idempotente
    [total] = attribution.totals(northwind.conn, tenant)
    assert (total.sales, float(total.amount), total.links) == (1, 149.0, 1)


def test_el_identificador_no_tiene_datos_del_cliente() -> None:
    case_id = uuid.uuid4()
    ref = attribution.conversation_ref(case_id)
    assert ref.startswith("ea-") and len(ref) == 15 and case_id.hex.startswith(ref[3:])


def test_shopify_lleva_la_conversacion_al_pedido_y_la_encuentra() -> None:
    order = {
        "name": "#1050",
        "cancelledAt": None,
        "currencyCode": "ARS",
        "totalPriceSet": {"shopMoney": {"amount": "85000.0", "currencyCode": "ARS"}},
        "customAttributes": [{"key": "asistente_ref", "value": "ea-abc123def456"}],
    }
    store = FakeShopify(
        products=[product("Campera", [variant(808950810, "CI-M", "M", None)])], orders=[order]
    )
    runtime = ToolRuntime(shopify.client("mitienda", "shpat_123", store.transport))
    made = runtime.call(
        "POST", "/checkouts", {"items": [{"sku": "CI-M", "quantity": 1}], "ref": "ea-abc123def456"}
    )
    assert parse_qs(urlsplit(made.data["url"]).query)["attributes[asistente_ref]"] == [
        "ea-abc123def456"
    ]
    found = runtime.call("GET", "/checkouts/{ref}", {"ref": "ea-abc123def456"}).data
    assert found == {
        "ref": "ea-abc123def456",
        "status": "completed",
        "order_id": "1050",
        "total": 85000.0,
        "currency": "ARS",
    }
    assert runtime.call("GET", "/checkouts/{ref}", {"ref": "ea-otro"}).status_code == 404


# -- Métricas -----------------------------------------------------------------------------------
@pytest.fixture
def acme(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("PANEL_EMAIL", ACME_EMAIL)
    monkeypatch.setenv("PANEL_PASSWORD", ACME_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield stack
    finally:
        app.dependency_overrides.clear()


def test_metricas_muestra_las_ventas_atribuidas(conn: psycopg.Connection, acme: Any) -> None:
    from app.db.connection import tenant_scope

    tenant = acme.seed.tenant_id
    with tenant_scope(conn, tenant):
        row = conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (tenant, next(iter(acme.seed.contacts.values()))),
        ).fetchone()
    assert row is not None
    attribution.record(
        conn,
        tenant,
        row[0],
        "ea-1",
        {"url": "https://t/cart/1:1", "total": 45000, "currency": "ARS"},
    )
    with tenant_scope(conn, tenant):
        conn.execute(
            "update checkout_links set amount = 45000, order_id = '1001', completed_at = now()"
        )
    client = TestClient(app)
    client.post("/panel/login", data={"email": ACME_EMAIL, "password": ACME_PASSWORD})
    page = client.get("/panel/metricas").text
    assert "Ventas atribuidas al agente" in page
    assert "ARS 45,000.00" in page and "Actualizar ventas" in page
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    refreshed = client.post(
        "/panel/metricas/ventas", data={"csrf": token.group(1)}, follow_redirects=False
    )
    assert refreshed.status_code == 303
