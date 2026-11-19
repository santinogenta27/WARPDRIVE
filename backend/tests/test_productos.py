"""Hito 10, punto 1 — test de aceptación: productos con precio, stock y variantes reales
(decisión 059).

- La API canónica de tienda suma `GET /products`: cada producto con su precio y sus variantes
  (talle, color) con el stock de ese momento. Northwind la implementa y los conectores de Shopify
  y Tiendanube la traducen.
- La herramienta `buscar_productos` sale del OpenAPI como las demás, pasa por el guardián y no
  pide verificar la identidad (es una consulta de preventa).
- Northwind tiene el tipo de caso `shopping` para las preguntas de preventa.
- Precio y stock salen siempre de una herramienta: en un turno con la búsqueda de productos, un
  monto o una afirmación de stock sin una herramienta que lo respalde se rechaza en código.
"""

import json
import uuid
from typing import Any

import httpx
import psycopg
import pytest
from shopify_admin import FakeShopify, product, variant
from simulated.stack import build_simulated_stack

from app.agent import grounding
from app.connectors import shopify, tiendanube
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.policy.guardian import ContactContext, Guardian
from app.tools.runtime import ToolRuntime
from northwind_api import create_app, search_products


# -- 1. la API de la tienda ------------------------------------------------------------------
def test_la_api_de_northwind_busca_productos_con_precio_y_stock_por_variante() -> None:
    from fastapi.testclient import TestClient

    api = TestClient(create_app())
    found = api.get("/products", params={"query": "rain jacket", "size": "M"}).json()["products"]
    assert [p["name"] for p in found] == ["Trail Runner Jacket"]
    jacket = found[0]
    assert jacket["price"] == 149.0 and jacket["currency"] == "USD"
    stock = {v["color"]: v["stock"] for v in jacket["variants"]}
    assert stock == {"Lake Blue": 0, "Black": 3, "Moss": 0}  # solo talle M
    assert jacket["in_stock"] is True
    assert jacket["variants"][1]["sku"] == "NW-TRJ-BLK-M"  # como los SKU de los pedidos
    assert len(api.get("/products").json()["products"]) == 8  # sin búsqueda, todo el catálogo


def test_la_busqueda_entiende_plurales_y_no_inventa_productos() -> None:
    import json as _json

    from northwind_api import PRODUCTS_PATH

    catalog = _json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    assert [p["name"] for p in search_products(catalog, "jackets")] == ["Trail Runner Jacket"]
    assert search_products(catalog, "surfboard") == []


# -- 2. conectores ---------------------------------------------------------------------------
SHOPIFY_PRODUCT = product(
    variants=[
        variant(808950810, "CI-M-NEG", "M", 4, color="Negro"),
        variant(808950811, "CI-L-NEG", "L", -2, color="Negro"),
    ]
)


def test_shopify_traduce_productos_con_variantes_y_stock() -> None:
    store = FakeShopify(products=[SHOPIFY_PRODUCT])
    runtime = ToolRuntime(shopify.client("mitienda", "shpat_123", store.transport))
    found = runtime.call("GET", "/products", {"query": "campera"}).data["products"]
    assert len(found) == 1
    product = found[0]
    assert (product["name"], product["price"], product["currency"]) == (
        "Campera Impermeable",
        85000.0,
        "ARS",
    )
    assert [(v["size"], v["color"], v["stock"]) for v in product["variants"]] == [
        ("M", "Negro", 4),
        ("L", "Negro", 0),  # sobrevendido: sin stock, nunca negativo
    ]
    only_l = runtime.call("GET", "/products", {"query": "campera", "size": "L"}).data
    assert only_l["products"][0]["in_stock"] is False
    assert runtime.call("GET", "/products", {"query": "zapatilla"}).data == {"products": []}


def test_tiendanube_traduce_productos_y_el_stock_sin_limite() -> None:
    product = {
        "id": 111,
        "name": {"es": "Buzo Polar"},
        "description": {"es": "<p>Abrigado.</p>"},
        "attributes": [{"es": "Talle"}],
        "variants": [
            {"id": 1, "sku": "BP-S", "price": "30000.00", "stock": None, "values": [{"es": "S"}]},
            {"id": 2, "sku": "BP-M", "price": "30000.00", "stock": 0, "values": [{"es": "M"}]},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/products"):
            assert request.url.params["q"] == "buzo"
            return httpx.Response(200, json=[product])
        if request.url.path.endswith("/store"):
            return httpx.Response(200, json={"main_currency": "ARS"})
        return httpx.Response(404, json={})

    runtime = ToolRuntime(tiendanube.client("123456", "tn_123", httpx.MockTransport(handler)))
    found = runtime.call("GET", "/products", {"query": "buzo"}).data["products"][0]
    assert found["name"] == "Buzo Polar" and found["currency"] == "ARS"
    assert [(v["size"], v["stock"]) for v in found["variants"]] == [("S", None), ("M", 0)]
    assert found["in_stock"] is True  # stock null: sin límite


# -- 3. la herramienta, el guardián y el tipo de caso de Northwind ------------------------------
@pytest.fixture
def northwind(conn: psycopg.Connection) -> Any:
    return build_simulated_stack(conn, company="northwind")


def test_buscar_productos_sale_del_openapi_y_no_pide_identidad(northwind: Any) -> None:
    from app.companies import review

    tenant = northwind.seed.tenant_id
    tools = {t.name: t for t in review.tools(northwind.conn, tenant)}
    manifest = tools["buscar_productos"].manifest
    assert tools["buscar_productos"].approved
    assert manifest["risk"] == "low" and manifest["requires_verified_contact"] is False
    assert manifest["upstream"] == {"method": "GET", "path": "/products"}
    kinds = {
        k.name: review.parse_instructions(k.instructions).get("herramientas") or []
        for k in review.case_types(northwind.conn, tenant)
    }
    assert "buscar_productos" in kinds["shopping"]

    guardian = Guardian(northwind.conn, ToolRuntime(northwind.acme))
    with tenant_scope(northwind.conn, tenant):
        row = northwind.conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (tenant, northwind.seed.contacts["sophia"]),
        ).fetchone()
    assert row is not None
    decision = guardian.execute(
        tenant_id=tenant,
        case_id=row[0],
        contact=ContactContext(northwind.seed.contacts["sophia"], hash_phone("+1555"), False),
        tool_name="buscar_productos",
        tool_input={"query": "jacket", "size": "M"},
        allowed={"buscar_productos"},
    )
    assert decision.status == "executed", decision
    assert decision.result["data"]["products"][0]["name"] == "Trail Runner Jacket"


# -- 4. precio y stock solo de una herramienta --------------------------------------------------
PRODUCTS = json.dumps(
    {"ok": True, "data": {"products": [{"name": "Trail Runner Jacket", "price": 149.0}]}}
)


@pytest.mark.parametrize(
    ("message", "tools", "ok"),
    [
        ("The Trail Runner Jacket is $149 and it's in stock in Black.", [PRODUCTS], True),
        ("The Trail Runner Jacket is $139.", [PRODUCTS], False),  # precio inventado
        ("The Trail Runner Jacket is $149.", [], False),  # precio sin herramienta (del catálogo)
        ("Yes, we have it in stock in size M.", [], False),  # stock sin herramienta
        ("Shipping is free on orders over $75.", [], True),  # política, no precio de producto
    ],
)
def test_precio_y_stock_solo_de_una_herramienta(message: str, tools: list[str], ok: bool) -> None:
    documents = {"shipping": "Shipping is free on orders over $75."}
    verdict = grounding.check_sales(message, tools, documents, language="en")
    assert verdict.ok is ok, verdict
    if not ok:
        assert verdict.kind in (grounding.PRICE, grounding.STOCK)


def test_el_agente_recomienda_una_variante_con_stock(northwind: Any) -> None:
    result = northwind.send("sophia", "Do you have a rain jacket in size M?")
    assert result.case_type == "shopping"
    assert [t.name for t in result.tool_calls] == ["buscar_productos"]
    assert result.action == "responder"
    assert "$149" in result.message and "Black" in result.message
    assert not result.verification_requested
    assert "Lake Blue" not in result.message or "out of stock" in result.message


def test_un_precio_inventado_se_rechaza_y_se_corrige(conn: psycopg.Connection) -> None:
    from app.audit.log import reconstruct_case

    stack = build_simulated_stack(conn, mode="inventa_precio", company="northwind")
    result = stack.send("sophia", "How much is the Trail Runner Jacket in size M?")
    events = reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id)
    rejected = [e.payload for e in events if e.kind == "grounding_rejected"]
    assert rejected and "precio" in rejected[0]["reason"]
    assert "$149" in result.message and "$99" not in result.message


def test_un_producto_nuevo_no_rompe_el_id_de_caso(northwind: Any) -> None:
    # La búsqueda no depende de un pedido ni de la verificación: un cliente nuevo pregunta.
    northwind.add_contact("nuevo:productos")
    result = northwind.send("nuevo:productos", "Do you sell wool beanies?")
    assert isinstance(result.case_id, uuid.UUID)
    assert result.action == "responder" and "Wool Beanie" in result.message
