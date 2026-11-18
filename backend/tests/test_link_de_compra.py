"""Hito 10, punto 2 — test de aceptación: link de compra (decisión 060).

- La API canónica de tienda suma `POST /checkouts`: arma el carrito con las variantes elegidas y
  devuelve el link del checkout de la tienda. No cobra ni reserva nada.
- Northwind lo implementa; Shopify devuelve su permalink de carrito
  (`https://tienda/cart/{variante}:{cantidad}`); Tiendanube, la página del producto con la
  variante (no tiene carrito armado por API).
- La herramienta `armar_carrito` sale del OpenAPI con riesgo bajo, sin pedir identidad y sin
  mover dinero. El agente nunca cobra: decir que cobró o que hizo la compra se rechaza en código,
  y un link que no salió de una herramienta, también.
"""

from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.agent import conduct, grounding
from app.connectors import shopify, tiendanube
from app.tools.runtime import ToolRuntime
from northwind_api import create_app


# -- 1. la API de la tienda ------------------------------------------------------------------
def test_northwind_arma_el_carrito_y_devuelve_el_link_de_checkout() -> None:
    api = TestClient(create_app())
    made = api.post("/checkouts", json={"items": [{"sku": "NW-TRJ-BLK-M", "quantity": 1}]})
    assert made.status_code == 201
    checkout = made.json()
    assert checkout["url"] == "https://shop.northwind.example/cart/NW-TRJ-BLK-M:1"
    assert (checkout["total"], checkout["currency"]) == (149.0, "USD")
    assert checkout["items"][0]["color"] == "Black"


def test_sin_stock_o_un_sku_que_no_existe_no_arma_el_carrito() -> None:
    api = TestClient(create_app())
    gone = api.post("/checkouts", json={"items": [{"sku": "NW-TRJ-BLU-M", "quantity": 1}]})
    assert gone.status_code == 409 and gone.json()["code"] == "out_of_stock"
    unknown = api.post("/checkouts", json={"items": [{"sku": "NW-XXX", "quantity": 1}]})
    assert unknown.status_code == 409 and unknown.json()["code"] == "unknown_sku"


def test_el_link_puede_apuntar_a_otra_tienda() -> None:
    api = TestClient(create_app(checkout_base="http://localhost:8000/demo/shop/"))
    made = api.post("/checkouts", json={"items": [{"sku": "NW-WBN-NVY-OS", "quantity": 2}]})
    assert made.json()["url"] == "http://localhost:8000/demo/shop/cart/NW-WBN-NVY-OS:2"
    assert made.json()["total"] == 48.0


# -- 2. conectores ---------------------------------------------------------------------------
PRODUCT = {
    "id": 632910392,
    "title": "Campera Impermeable",
    "options": [{"name": "Talle"}],
    "variants": [
        {
            "id": 808950810,
            "sku": "CI-M",
            "price": "85000.00",
            "option1": "M",
            "inventory_management": "shopify",
            "inventory_quantity": 4,
        },
        {
            "id": 808950811,
            "sku": "CI-L",
            "price": "85000.00",
            "option1": "L",
            "inventory_management": "shopify",
            "inventory_quantity": 0,
        },
    ],
}


def _shopify() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "el link de compra no crea nada en Shopify"
        path = request.url.path.removeprefix("/admin/api/2025-01")
        if path == "/products.json":
            return httpx.Response(200, json={"products": [PRODUCT]})
        if path == "/shop.json":
            return httpx.Response(200, json={"shop": {"currency": "ARS"}})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def test_shopify_devuelve_el_permalink_de_carrito() -> None:
    runtime = ToolRuntime(shopify.client("mitienda", "shpat_123", _shopify()))
    made = runtime.call("POST", "/checkouts", {"items": [{"sku": "CI-M", "quantity": 2}]})
    assert made.status_code == 201
    assert made.data["url"] == "https://mitienda.myshopify.com/cart/808950810:2"
    assert (made.data["total"], made.data["currency"]) == (170000.0, "ARS")
    gone = runtime.call("POST", "/checkouts", {"items": [{"sku": "CI-L", "quantity": 1}]})
    assert gone.status_code == 409


def test_tiendanube_devuelve_la_pagina_del_producto_con_la_variante() -> None:
    product = {
        "id": 111,
        "name": {"es": "Buzo Polar"},
        "canonical_url": "https://mitienda.mitiendanube.com/productos/buzo-polar/",
        "attributes": [{"es": "Talle"}],
        "variants": [
            {"id": 7, "sku": "BP-S", "price": "30000.00", "stock": None, "values": [{"es": "S"}]}
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/products/sku/BP-S"):
            return httpx.Response(200, json=product)
        if request.url.path.endswith("/store"):
            return httpx.Response(200, json={"main_currency": "ARS"})
        return httpx.Response(404, json={})

    runtime = ToolRuntime(tiendanube.client("123456", "tn_123", httpx.MockTransport(handler)))
    made = runtime.call("POST", "/checkouts", {"items": [{"sku": "BP-S", "quantity": 1}]})
    assert made.status_code == 201
    assert made.data["url"] == "https://mitienda.mitiendanube.com/productos/buzo-polar/?variant=7"
    two = runtime.call(
        "POST",
        "/checkouts",
        {"items": [{"sku": "BP-S", "quantity": 1}, {"sku": "BP-M", "quantity": 1}]},
    )
    assert two.status_code == 409 and two.data["code"] == "one_product_per_link"


# -- 3. la herramienta y el agente -----------------------------------------------------------
@pytest.fixture
def northwind(conn: psycopg.Connection) -> Any:
    return build_simulated_stack(conn, company="northwind")


def test_armar_carrito_no_mueve_dinero_ni_pide_identidad(northwind: Any) -> None:
    from app.companies import review

    tenant = northwind.seed.tenant_id
    tools = {t.name: t for t in review.tools(northwind.conn, tenant)}
    manifest = tools["armar_carrito"].manifest
    assert tools["armar_carrito"].approved
    assert manifest["risk"] == "low" and manifest["requires_verified_contact"] is False
    assert manifest["upstream"] == {"method": "POST", "path": "/checkouts"}
    assert "amount_from" not in manifest and manifest["approval_over"] is None
    kinds = {
        k.name: review.parse_instructions(k.instructions).get("herramientas") or []
        for k in review.case_types(northwind.conn, tenant)
    }
    assert kinds["shopping"] == ["buscar_productos", "armar_carrito"]


def test_el_cliente_elige_y_recibe_el_link_de_compra(northwind: Any) -> None:
    first = northwind.send("sophia", "Do you have a rain jacket in size M?")
    assert "Black" in first.message
    result = northwind.send("sophia", "Great, I'll take the black one")
    assert result.case_id == first.case_id and result.case_type == "shopping"
    executed = [(t.name, t.input) for t in result.tool_calls if t.status == "executed"]
    assert ("armar_carrito", {"items": [{"sku": "NW-TRJ-BLK-M", "quantity": 1}]}) in executed
    assert "https://shop.northwind.example/cart/NW-TRJ-BLK-M:1" in result.message
    assert "$149" in result.message
    assert not conduct.claims_payment(result.message)


# -- 4. nunca cobra, nunca inventa un link ------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("I've charged your card $149.", True),
        ("Your order has been placed!", True),
        ("Listo, ya te cobramos la campera.", True),
        ("Here's your link: https://shop.example/cart/1:1. You pay on our checkout.", False),
        ("Te paso el link para que pagues en la tienda.", False),
    ],
)
def test_decir_que_cobro_o_compro(text: str, claims: bool) -> None:
    assert conduct.claims_payment(text) is claims


def test_un_link_inventado_se_rechaza() -> None:
    tool = '{"ok": true, "data": {"url": "https://shop.northwind.example/cart/NW-TRJ-BLK-M:1"}}'
    ok = grounding.check_sales(
        "Buy it here: https://shop.northwind.example/cart/NW-TRJ-BLK-M:1.", [tool], {}, "en"
    )
    assert ok.ok, ok
    made_up = grounding.check_sales("Buy it here: https://northwind.shop/jacket", [tool], {}, "en")
    assert not made_up.ok and made_up.kind == grounding.LINK
