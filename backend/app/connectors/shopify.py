"""Conector de Shopify (decisión 057): las herramientas de una tienda sin programar.

El agente y el guardián hablan con una **API canónica de tienda** (la misma de la demo de
Northwind: `GET /orders/{order_id}`, `POST /orders/{order_id}/refunds`, cupones, búsqueda de
clientes por email). Este módulo es un transporte de httpx que traduce cada pedido canónico a la
API Admin REST de Shopify y la respuesta de vuelta. Así las herramientas se generan del OpenAPI
canónico, con los mismos controles (identidad, dueño, montos, aprobación), y el guardián no
cambia.

* **Pedido:** el número que ve el cliente (`1001` o `#1001`), buscado por `name`.
* **Dueño:** el teléfono del cliente o, si no tiene, su id de Shopify: el mismo valor sale en la
  búsqueda por email y en el pedido, así el control de dueño compara lo mismo.
* **Reembolso:** sobre la transacción cobrada del pedido, por el monto pedido.
* **Cupón:** una regla de precio de un solo uso con su código.
"""

import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

API_VERSION = "2025-01"
CANONICAL_BASE = "https://tienda.local"
SHOP = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
STATUS = {
    ("paid", None): "paid",
    ("paid", "partial"): "shipped",
    ("paid", "fulfilled"): "shipped",
    ("refunded", None): "refunded",
    ("refunded", "fulfilled"): "refunded",
    ("partially_refunded", "fulfilled"): "shipped",
    ("pending", None): "pending",
}


def clean_shop(value: str) -> str:
    shop = value.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if "." not in shop:
        shop = f"{shop}.myshopify.com"
    if not SHOP.match(shop):
        raise ValueError(
            "La tienda tiene que ser tu dominio de Shopify, como mitienda.myshopify.com."
        )
    return shop


def _json(status: int, data: Any) -> httpx.Response:
    return httpx.Response(status, json=data)


def _owner(customer: dict[str, Any] | None) -> str | None:
    if not customer:
        return None
    return str(customer.get("phone") or customer.get("id") or "") or None


def order_view(order: dict[str, Any]) -> dict[str, Any]:
    """Un pedido de Shopify con la forma del pedido canónico (el Order del OpenAPI)."""
    fulfillments = order.get("fulfillments") or []
    last = fulfillments[-1] if fulfillments else {}
    status = STATUS.get(
        (str(order.get("financial_status")), order.get("fulfillment_status")),
        "cancelled" if order.get("cancelled_at") else str(order.get("financial_status")),
    )
    if last.get("shipment_status") == "delivered":
        status = "delivered"
    address = order.get("shipping_address") or {}
    return {
        "id": str(order.get("name", "")).lstrip("#"),
        "customer_phone": _owner(order.get("customer")),
        "customer_name": (order.get("customer") or {}).get("first_name"),
        "status": status,
        "items": [
            {
                "sku": item.get("sku"),
                "name": item.get("title"),
                "size": item.get("variant_title"),
                "quantity": item.get("quantity"),
                "unit_price": float(item.get("price") or 0),
            }
            for item in order.get("line_items") or []
        ],
        "total": float(order.get("total_price") or 0),
        "currency": order.get("currency"),
        "shipping_address": ", ".join(
            str(address[k]) for k in ("address1", "city", "province") if address.get(k)
        ),
        "created_at": order.get("created_at"),
        "shipped_at": last.get("created_at"),
        "carrier": last.get("tracking_company"),
        "tracking_number": last.get("tracking_number"),
    }


class ShopifyTransport(httpx.BaseTransport):
    def __init__(self, shop: str, token: str, upstream: httpx.BaseTransport | None = None) -> None:
        self._api = httpx.Client(
            base_url=f"https://{clean_shop(shop)}/admin/api/{API_VERSION}",
            headers={"X-Shopify-Access-Token": token},
            transport=upstream,
            timeout=15.0,
            follow_redirects=False,
        )

    # -- traducción ------------------------------------------------------------------------
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        body = json.loads(request.content) if request.content else {}
        if request.method == "GET" and path == "/customers":
            return self._customers(query.get("email", ""))
        match = re.match(r"^/orders/([^/]+)(?:/(\w[\w-]*))?$", path)
        if match is None:
            return _json(404, {"code": "not_found", "message": "Operación no disponible."})
        order = self._order(match.group(1))
        if order is None:
            return _json(404, {"code": "order_not_found", "message": "No existe ese pedido."})
        action = match.group(2)
        if request.method == "GET" and action is None:
            return _json(200, order_view(order))
        if request.method == "POST" and action == "refunds":
            return self._refund(order, float(body.get("amount") or 0))
        if request.method == "POST" and action == "coupons":
            return self._coupon(order, float(body.get("percent") or 0))
        return _json(404, {"code": "not_found", "message": "Operación no disponible."})

    def _customers(self, email: str) -> httpx.Response:
        found = self._api.get("/customers/search.json", params={"query": f"email:{email}"})
        if found.status_code != 200:
            return _json(found.status_code, {"message": "Shopify no respondió."})
        customers = [
            {"id": str(c.get("id")), "email": c.get("email"), "phone": _owner(c)}
            for c in found.json().get("customers") or []
            if str(c.get("email", "")).lower() == email.lower()
        ]
        return _json(200, {"customers": customers})

    def _order(self, number: str) -> dict[str, Any] | None:
        name = f"#{number.lstrip('#')}"
        found = self._api.get("/orders.json", params={"name": name, "status": "any"})
        if found.status_code != 200:
            return None
        orders = [o for o in found.json().get("orders") or [] if o.get("name") == name]
        return orders[0] if len(orders) == 1 else None

    def _refund(self, order: dict[str, Any], amount: float) -> httpx.Response:
        found = self._api.get(f"/orders/{order['id']}/transactions.json")
        paid = [
            t
            for t in (found.json().get("transactions") or [] if found.status_code == 200 else [])
            if t.get("kind") in ("sale", "capture") and t.get("status") == "success"
        ]
        if not paid:
            return _json(409, {"code": "not_paid", "message": "El pedido no tiene un cobro."})
        refund = {
            "refund": {
                "currency": order.get("currency"),
                "notify": True,
                "note": "Reembolso aprobado desde el asistente",
                "transactions": [
                    {
                        "parent_id": paid[0]["id"],
                        "amount": f"{amount:.2f}",
                        "kind": "refund",
                        "gateway": paid[0].get("gateway"),
                    }
                ],
            }
        }
        done = self._api.post(f"/orders/{order['id']}/refunds.json", json=refund)
        if done.status_code not in (200, 201):
            return _json(done.status_code, {"message": "Shopify rechazó el reembolso."})
        created = done.json().get("refund") or {}
        return _json(
            201,
            {
                "id": str(created.get("id")),
                "order_id": str(order.get("name", "")).lstrip("#"),
                "amount": amount,
                "currency": order.get("currency"),
                "status": "processed",
                "created_at": created.get("created_at"),
            },
        )

    def _coupon(self, order: dict[str, Any], percent: float) -> httpx.Response:
        code = f"GRACIAS-{secrets.token_hex(3).upper()}"
        rule = {
            "price_rule": {
                "title": code,
                "target_type": "line_item",
                "target_selection": "all",
                "allocation_method": "across",
                "value_type": "percentage",
                "value": f"-{percent:.1f}",
                "customer_selection": "all",
                "usage_limit": 1,
                "once_per_customer": True,
                "starts_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        }
        made = self._api.post("/price_rules.json", json=rule)
        if made.status_code not in (200, 201):
            return _json(made.status_code, {"message": "Shopify rechazó el cupón."})
        rule_id = made.json()["price_rule"]["id"]
        coded = self._api.post(
            f"/price_rules/{rule_id}/discount_codes.json", json={"discount_code": {"code": code}}
        )
        if coded.status_code not in (200, 201):
            return _json(coded.status_code, {"message": "Shopify rechazó el código del cupón."})
        return _json(
            201,
            {
                "code": code,
                "kind": "percent",
                "order_id": str(order.get("name", "")).lstrip("#"),
                "percent": percent,
                "applies_to": "next_order",
            },
        )


def client(shop: str, token: str, upstream: httpx.BaseTransport | None = None) -> httpx.Client:
    """El cliente HTTP que usa el runtime de herramientas: habla la API canónica."""
    return httpx.Client(base_url=CANONICAL_BASE, transport=ShopifyTransport(shop, token, upstream))
