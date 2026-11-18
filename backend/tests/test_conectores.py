"""Hito 9, tarea 10 — test de aceptación: conectores de Shopify y Tiendanube (decisión 057).

Una tienda conecta su Shopify (dominio y token) o su Tiendanube (id y token) sin programar: el
agente usa la misma API canónica de tienda que la demo, con las mismas herramientas y controles
del guardián, y el conector la traduce a la API de cada plataforma.
"""

import json
from typing import Any

import httpx
import psycopg
import pytest

from app.connectors import shopify, tiendanube
from app.contacts import hash_phone
from app.tools.runtime import ToolRuntime

ORDER = {
    "id": 450789469,
    "name": "#1001",
    "email": "ana@example.com",
    "financial_status": "paid",
    "fulfillment_status": "fulfilled",
    "total_price": "45000.00",
    "currency": "ARS",
    "created_at": "2026-11-10T10:00:00-03:00",
    "customer": {"id": 207119551, "email": "ana@example.com", "phone": None, "first_name": "Ana"},
    "shipping_address": {"address1": "Corrientes 1234", "city": "CABA", "province": "CABA"},
    "line_items": [{"sku": "CP-1", "title": "Campera", "quantity": 1, "price": "45000.00"}],
    "fulfillments": [
        {
            "created_at": "2026-11-11T09:00:00-03:00",
            "tracking_company": "Andreani",
            "tracking_number": "AR123",
            "shipment_status": "in_transit",
        }
    ],
}


def _shopify() -> tuple[httpx.MockTransport, list[tuple[str, str, Any]]]:
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        assert request.headers["X-Shopify-Access-Token"] == "shpat_123"
        assert request.url.host == "mitienda.myshopify.com"
        path = request.url.path.removeprefix("/admin/api/2025-01")
        if path == "/customers/search.json":
            return httpx.Response(200, json={"customers": [ORDER["customer"]]})
        if path == "/orders.json":
            name = request.url.params["name"]
            return httpx.Response(200, json={"orders": [ORDER] if name == "#1001" else []})
        if path == "/orders/450789469/transactions.json":
            tx = {"id": 389404469, "kind": "sale", "status": "success", "gateway": "mp"}
            return httpx.Response(200, json={"transactions": [tx]})
        if path == "/orders/450789469/refunds.json":
            return httpx.Response(201, json={"refund": {"id": 509562969}})
        if path == "/price_rules.json":
            return httpx.Response(201, json={"price_rule": {"id": 507328175}})
        if path == "/price_rules/507328175/discount_codes.json":
            return httpx.Response(
                201, json={"discount_code": {"code": body["discount_code"]["code"]}}
            )
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler), calls


def test_shopify_consulta_el_pedido_con_la_forma_canonica() -> None:
    upstream, _ = _shopify()
    runtime = ToolRuntime(shopify.client("mitienda", "shpat_123", upstream))
    found = runtime.call("GET", "/orders/{order_id}", {"order_id": "1001"})
    assert found.ok
    order = found.data
    assert order["id"] == "1001" and order["status"] == "shipped" and order["total"] == 45000.0
    assert order["tracking_number"] == "AR123" and order["carrier"] == "Andreani"
    assert runtime.call("GET", "/orders/{order_id}", {"order_id": "9999"}).status_code == 404


def test_shopify_el_dueno_coincide_entre_la_busqueda_por_email_y_el_pedido() -> None:
    upstream, _ = _shopify()
    runtime = ToolRuntime(shopify.client("mitienda.myshopify.com", "shpat_123", upstream))
    customers = runtime.call("GET", "/customers", {"email": "ana@example.com"}).data["customers"]
    order = runtime.call("GET", "/orders/{order_id}", {"order_id": "1001"}).data
    assert len(customers) == 1
    assert hash_phone(customers[0]["phone"]) == hash_phone(order["customer_phone"])


def test_shopify_reembolsa_y_crea_cupones() -> None:
    upstream, calls = _shopify()
    runtime = ToolRuntime(shopify.client("mitienda", "shpat_123", upstream))
    refund = runtime.call(
        "POST", "/orders/{order_id}/refunds", {"order_id": "1001", "amount": 45000}, key="k1"
    )
    assert refund.status_code == 201 and refund.data["amount"] == 45000
    sent = next(b for m, p, b in calls if p.endswith("/refunds.json"))
    assert sent["refund"]["transactions"][0] == {
        "parent_id": 389404469,
        "amount": "45000.00",
        "kind": "refund",
        "gateway": "mp",
    }
    coupon = runtime.call("POST", "/orders/{order_id}/coupons", {"order_id": "1001", "percent": 10})
    assert coupon.status_code == 201 and coupon.data["code"].startswith("GRACIAS-")


def test_shopify_rechaza_un_dominio_que_no_es_de_shopify() -> None:
    with pytest.raises(ValueError):
        shopify.clean_shop("https://evil.example.com")
    assert shopify.clean_shop("https://MiTienda.myshopify.com/") == "mitienda.myshopify.com"


def test_tiendanube_consulta_pedidos_y_el_reembolso_lo_hace_una_persona() -> None:
    order = {
        "number": 1001,
        "customer": {"id": 77, "email": "ana@example.com", "phone": "+5491155550000"},
        "payment_status": "paid",
        "shipping_status": "shipped",
        "total": "45000.00",
        "currency": "ARS",
        "products": [{"sku": "CP-1", "name": "Campera", "quantity": 1, "price": "45000.00"}],
        "shipping_tracking_number": "TN1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authentication"] == "bearer tn_123"
        assert request.url.path.startswith("/v1/123456/")
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json=[order])
        if request.url.path.endswith("/customers"):
            return httpx.Response(200, json=[order["customer"]])
        if request.url.path.endswith("/coupons"):
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404, json={})

    runtime = ToolRuntime(tiendanube.client("123456", "tn_123", httpx.MockTransport(handler)))
    found = runtime.call("GET", "/orders/{order_id}", {"order_id": "1001"}).data
    assert found["status"] == "shipped" and found["customer_phone"] == "+5491155550000"
    refund = runtime.call(
        "POST", "/orders/{order_id}/refunds", {"order_id": "1001", "amount": 10}, key="k"
    )
    assert refund.status_code == 409 and "persona" in refund.data["message"]
    coupon = runtime.call("POST", "/orders/{order_id}/coupons", {"order_id": "1001", "percent": 10})
    assert coupon.status_code == 201


def test_conectar_shopify_deja_la_conexion_y_las_herramientas_sin_aprobar(
    conn: psycopg.Connection,
) -> None:
    from app.companies import review
    from app.companies.connection import _secret, load, test_connection
    from app.companies.create import create_company
    from app.connectors import platforms

    tenant = create_company(
        conn, "Tienda Sur", "duena@tiendasur.example", "clave-sur-123"
    ).tenant_id
    platforms.connect(conn, tenant, "shopify", "mitienda", "shpat_123")
    found = load(conn, tenant)
    assert found is not None and found.base_url == "shopify:mitienda.myshopify.com"
    assert _secret(conn, tenant) == "shpat_123"
    tools = {t.name: t for t in review.tools(conn, tenant)}
    assert {"consultar_pedido", "iniciar_reembolso", "ofrecer_cupon"} <= set(tools)
    assert not any(t.approved for t in tools.values())
    assert tools["iniciar_reembolso"].manifest["risk"] == "high"
    assert tools["consultar_pedido"].manifest["requires_verified_contact"]

    upstream, _ = _shopify()
    result = test_connection(
        conn, tenant, client_factory=lambda c, s: platforms.client(c.base_url or "", s, upstream)
    )
    assert result["ok"], result

    from app.companies.connection import probe_tools

    probes = {
        p["herramienta"]: p
        for p in probe_tools(
            conn,
            tenant,
            "1001",
            client_factory=lambda c, s: platforms.client(c.base_url or "", s, _shopify()[0]),
        )
    }
    assert probes["consultar_pedido"]["ok"] is True
    assert probes["iniciar_reembolso"]["ok"] is None  # modifica datos: no se prueba en vivo
