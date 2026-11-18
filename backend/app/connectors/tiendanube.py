"""Conector de Tiendanube (decisión 057): la misma API canónica de tienda que Shopify.

* **Pedido:** el número que ve el cliente, buscado con `q`.
* **Dueño:** el teléfono del cliente o, si no tiene, su id de Tiendanube (ver `shopify.py`).
* **Cupón:** un cupón de un solo uso con su código.
* **Reembolso:** la API de Tiendanube no reembolsa. El pedido de reembolso responde que lo hace
  una persona: el guardián ya lo manda a aprobación y quien aprueba lo hace en Tiendanube.
"""

import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

API = "https://api.tiendanube.com/v1"
CANONICAL_BASE = "https://tienda.local"
USER_AGENT = "Enterprise Data AI (soporte@enterprisedataai.com)"
STATUS = {"unpacked": "paid", "unfulfilled": "paid", "fulfilled": "shipped", "shipped": "shipped"}


def _json(status: int, data: Any) -> httpx.Response:
    return httpx.Response(status, json=data)


def _owner(customer: dict[str, Any] | None) -> str | None:
    if not customer:
        return None
    return str(customer.get("phone") or customer.get("id") or "") or None


def clean_store(value: str) -> str:
    store = value.strip()
    if not store.isdigit():
        raise ValueError("El id de la tienda es un número (lo ves en la URL del administrador).")
    return store


def order_view(order: dict[str, Any]) -> dict[str, Any]:
    address = order.get("shipping_address") or {}
    status = STATUS.get(str(order.get("shipping_status")), str(order.get("payment_status")))
    if order.get("status") == "cancelled":
        status = "cancelled"
    if order.get("shipping_status") == "delivered":
        status = "delivered"
    return {
        "id": str(order.get("number", "")),
        "customer_phone": _owner(order.get("customer")),
        "customer_name": (order.get("customer") or {}).get("name"),
        "status": status,
        "items": [
            {
                "sku": item.get("sku"),
                "name": item.get("name"),
                "quantity": item.get("quantity"),
                "unit_price": float(item.get("price") or 0),
            }
            for item in order.get("products") or []
        ],
        "total": float(order.get("total") or 0),
        "currency": order.get("currency"),
        "shipping_address": " ".join(
            str(address[k]) for k in ("address", "number", "city") if address.get(k)
        ),
        "created_at": order.get("created_at"),
        "shipped_at": order.get("shipped_at"),
        "carrier": order.get("shipping_option"),
        "tracking_number": order.get("shipping_tracking_number"),
    }


class TiendanubeTransport(httpx.BaseTransport):
    def __init__(self, store: str, token: str, upstream: httpx.BaseTransport | None = None) -> None:
        self._api = httpx.Client(
            base_url=f"{API}/{clean_store(store)}",
            headers={"Authentication": f"bearer {token}", "User-Agent": USER_AGENT},
            transport=upstream,
            timeout=15.0,
            follow_redirects=False,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = json.loads(request.content) if request.content else {}
        if request.method == "GET" and url.path == "/customers":
            return self._customers(query.get("email", ""))
        match = re.match(r"^/orders/([^/]+)(?:/(\w[\w-]*))?$", url.path)
        if match is None:
            return _json(404, {"code": "not_found", "message": "Operación no disponible."})
        order = self._order(match.group(1))
        if order is None:
            return _json(404, {"code": "order_not_found", "message": "No existe ese pedido."})
        action = match.group(2)
        if request.method == "GET" and action is None:
            return _json(200, order_view(order))
        if request.method == "POST" and action == "coupons":
            return self._coupon(order, float(body.get("percent") or 0))
        if request.method == "POST" and action == "refunds":
            return _json(
                409,
                {
                    "code": "manual_refund",
                    "message": "Tiendanube no reembolsa por API: lo hace una persona del equipo.",
                },
            )
        return _json(404, {"code": "not_found", "message": "Operación no disponible."})

    def _customers(self, email: str) -> httpx.Response:
        found = self._api.get("/customers", params={"q": email})
        if found.status_code != 200:
            return _json(found.status_code, {"message": "Tiendanube no respondió."})
        customers = [
            {"id": str(c.get("id")), "email": c.get("email"), "phone": _owner(c)}
            for c in found.json() or []
            if str(c.get("email", "")).lower() == email.lower()
        ]
        return _json(200, {"customers": customers})

    def _order(self, number: str) -> dict[str, Any] | None:
        found = self._api.get("/orders", params={"q": number.lstrip("#")})
        if found.status_code != 200:
            return None
        orders = [o for o in found.json() or [] if str(o.get("number")) == number.lstrip("#")]
        return orders[0] if len(orders) == 1 else None

    def _coupon(self, order: dict[str, Any], percent: float) -> httpx.Response:
        code = f"GRACIAS{secrets.token_hex(3).upper()}"
        made = self._api.post(
            "/coupons",
            json={"code": code, "type": "percentage", "value": f"{percent:.2f}", "max_uses": 1},
        )
        if made.status_code not in (200, 201):
            return _json(made.status_code, {"message": "Tiendanube rechazó el cupón."})
        return _json(
            201,
            {
                "code": code,
                "kind": "percent",
                "order_id": str(order.get("number", "")),
                "percent": percent,
                "applies_to": "next_order",
            },
        )


def client(store: str, token: str, upstream: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=CANONICAL_BASE, transport=TiendanubeTransport(store, token, upstream)
    )
