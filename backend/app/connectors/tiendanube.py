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

from app import net
from app.connectors import shopify

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


def _text(value: Any) -> str:
    """Los textos de Tiendanube vienen por idioma (`{"es": "Campera"}`): el primero que haya."""
    if isinstance(value, dict):
        return str(next((v for v in value.values() if v), ""))
    return str(value or "")


def product_view(product: dict[str, Any], currency: str | None) -> dict[str, Any]:
    """Un producto de Tiendanube con la forma del producto canónico. Las variantes traen sus
    valores en el orden de `attributes` (talle, color); stock null es stock sin límite."""
    names = [_text(a).strip().lower() for a in product.get("attributes") or []]
    variants: list[dict[str, Any]] = []
    for variant in product.get("variants") or []:
        values = [_text(v) for v in variant.get("values") or []]
        by_name = dict(zip(names, values, strict=False))
        size = next((by_name[n] for n in names if n in shopify.SIZE_OPTIONS), None)
        color = next((by_name[n] for n in names if n in shopify.COLOR_OPTIONS), None)
        stock = variant.get("stock")
        variants.append(
            {
                "sku": str(variant.get("sku") or variant.get("id")),
                "size": str(size or ""),
                "color": str(color or ""),
                "stock": max(int(stock), 0) if stock is not None else None,
                "price": float(variant.get("promotional_price") or variant.get("price") or 0),
            }
        )
    prices = [v["price"] for v in variants]
    return {
        "code": str(product.get("id")),
        "name": _text(product.get("name")),
        "category": "",
        "description": re.sub(r"<[^>]+>", " ", _text(product.get("description"))).strip(),
        "price": min(prices) if prices else 0.0,
        "currency": currency or "",
        "in_stock": any(v["stock"] is None or v["stock"] > 0 for v in variants),
        "variants": variants,
    }


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
            transport=upstream or net.SafeTransport(),  # solo IPs públicas (decisión 076)
            timeout=15.0,
            follow_redirects=False,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = json.loads(request.content) if request.content else {}
        if request.method == "GET" and url.path == "/customers":
            return self._customers(query.get("email", ""))
        if request.method == "GET" and url.path == "/products":
            return self._products(query.get("query", ""), query.get("size"))
        if request.method == "POST" and url.path == "/checkouts":
            return self._checkout(body.get("items") or [], float(body.get("discount_percent") or 0))
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

    def _products(self, query: str, size: str | None) -> httpx.Response:
        """Busca con `q` de Tiendanube (nombre, SKU y etiquetas) y vuelve a filtrar por
        palabras, como en Shopify."""
        params: dict[str, Any] = {"published": "true", "per_page": 50}
        if query.strip():
            params["q"] = query.strip()
        found = self._api.get("/products", params=params)
        if found.status_code == 404:  # Tiendanube responde 404 cuando no hay resultados
            return _json(200, {"products": []})
        if found.status_code != 200:
            return _json(found.status_code, {"message": "Tiendanube no respondió."})
        store = self._api.get("/store")
        currency = store.json().get("main_currency") if store.status_code == 200 else None
        products = [
            shopify.only_size(product_view(p, currency), size)
            for p in found.json() or []
            if shopify.matches(query, f"{_text(p.get('name'))} {p.get('tags') or ''}")
        ]
        return _json(200, {"products": products})

    def _checkout(self, items: list[dict[str, Any]], discount: float = 0) -> httpx.Response:
        """Tiendanube no tiene un link de carrito armado por API: el equivalente es la página
        del producto con la variante elegida, donde el cliente toca "Comprar". Por eso, un
        producto por link."""
        if len(items) != 1:
            return _json(
                409,
                {
                    "code": "one_product_per_link",
                    "message": "En Tiendanube el link de compra es de a un producto.",
                },
            )
        sku, quantity = str(items[0].get("sku", "")), int(items[0].get("quantity") or 1)
        found = self._api.get(f"/products/sku/{sku}")
        if found.status_code != 200:
            return _json(409, {"code": "unknown_sku", "message": f"No hay un producto {sku}."})
        product = found.json()
        store = self._api.get("/store")
        currency = store.json().get("main_currency") if store.status_code == 200 else None
        view = product_view(product, currency)
        pairs = zip(product.get("variants") or [], view["variants"], strict=True)
        raw, variant = next(((r, v) for r, v in pairs if v["sku"] == sku), (None, None))
        if raw is None or variant is None:
            return _json(409, {"code": "unknown_sku", "message": f"No hay un producto {sku}."})
        if variant["stock"] is not None and variant["stock"] < quantity:
            return _json(409, {"code": "out_of_stock", "message": f"{view['name']} sin stock."})
        line = {
            "sku": sku,
            "name": view["name"],
            "size": variant["size"],
            "color": variant["color"],
            "quantity": quantity,
            "unit_price": variant["price"],
        }
        total = variant["price"] * quantity
        checkout: dict[str, Any] = {
            "url": f"{_text(product.get('canonical_url'))}?variant={raw.get('id')}",
            "items": [line],
            "total": round(total, 2),
            "currency": currency or "",
        }
        if discount:
            # La página del producto no aplica cupones: el cliente escribe el código al pagar.
            code = self._discount_code("OFERTA", discount)
            if isinstance(code, httpx.Response):
                return code
            checkout["total"] = round(total * (1 - discount / 100), 2)
            checkout.update({"discount_percent": discount, "discount_code": code})
        return _json(201, checkout)

    def _order(self, number: str) -> dict[str, Any] | None:
        found = self._api.get("/orders", params={"q": number.lstrip("#")})
        if found.status_code != 200:
            return None
        orders = [o for o in found.json() or [] if str(o.get("number")) == number.lstrip("#")]
        return orders[0] if len(orders) == 1 else None

    def _discount_code(self, prefix: str, percent: float) -> str | httpx.Response:
        """Un cupón de un solo uso con su código."""
        code = f"{prefix}{secrets.token_hex(3).upper()}"
        made = self._api.post(
            "/coupons",
            json={"code": code, "type": "percentage", "value": f"{percent:.2f}", "max_uses": 1},
        )
        if made.status_code not in (200, 201):
            return _json(made.status_code, {"message": "Tiendanube rechazó el cupón."})
        return code

    def _coupon(self, order: dict[str, Any], percent: float) -> httpx.Response:
        code = self._discount_code("GRACIAS", percent)
        if isinstance(code, httpx.Response):
            return code
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
