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
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

API_VERSION = "2025-01"
CANONICAL_BASE = "https://tienda.local"
SHOP = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
REF_ATTRIBUTE = "asistente_ref"  # atributo del carrito con la conversación (decisión 063)
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


_WORD = re.compile(r"[^\W_]+")
SIZE_OPTIONS = {"size", "talle", "talla", "tamaño"}
COLOR_OPTIONS = {"color", "colour"}


def words(text: str) -> set[str]:
    """Las palabras de una búsqueda o de un producto, sin plural simple ("jackets" -> "jacket")."""
    return {w.rstrip("s") for w in _WORD.findall(text.lower()) if len(w) > 2}


def matches(query: str, text: str) -> bool:
    wanted = words(query)
    return not wanted or bool(wanted & words(text))


def product_view(product: dict[str, Any], currency: str | None) -> dict[str, Any]:
    """Un producto de Shopify con la forma del producto canónico: precio y stock por variante
    tal como los tiene la tienda. Sin seguimiento de inventario, el stock es null (disponible)."""
    names = [str(o.get("name", "")).strip().lower() for o in product.get("options") or []]
    variants: list[dict[str, Any]] = []
    for variant in product.get("variants") or []:
        values = [variant.get(f"option{i}") for i in (1, 2, 3)]
        by_name = dict(zip(names, values, strict=False))
        size = next((by_name[n] for n in names if n in SIZE_OPTIONS), None)
        color = next((by_name[n] for n in names if n in COLOR_OPTIONS), None)
        tracked = variant.get("inventory_management") == "shopify"
        variants.append(
            {
                "sku": str(variant.get("sku") or variant.get("id")),
                "size": str(size or variant.get("title") or ""),
                "color": str(color or ""),
                "stock": max(int(variant.get("inventory_quantity") or 0), 0) if tracked else None,
                "price": float(variant.get("price") or 0),
            }
        )
    prices = [v["price"] for v in variants]
    return {
        "code": str(product.get("id")),
        "name": product.get("title"),
        "category": product.get("product_type") or "",
        "description": re.sub(r"<[^>]+>", " ", str(product.get("body_html") or "")).strip(),
        "price": min(prices) if prices else 0.0,
        "currency": currency or "",
        "in_stock": any(v["stock"] is None or v["stock"] > 0 for v in variants),
        "variants": variants,
    }


def only_size(view: dict[str, Any], size: str | None) -> dict[str, Any]:
    if not size:
        return view
    variants = [v for v in view["variants"] if v["size"].lower() == size.strip().lower()]
    return {
        **view,
        "variants": variants,
        "in_stock": any(v["stock"] is None or v["stock"] > 0 for v in variants),
    }


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
        if request.method == "GET" and path == "/products":
            return self._products(query.get("query", ""), query.get("size"))
        if request.method == "POST" and path == "/checkouts":
            return self._checkout(
                body.get("items") or [],
                float(body.get("discount_percent") or 0),
                str(body.get("ref") or ""),
            )
        if request.method == "GET" and path.startswith("/checkouts/"):
            return self._purchase(path.removeprefix("/checkouts/"))
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

    def _products(self, query: str, size: str | None) -> httpx.Response:
        """Los productos activos (hasta 250) filtrados por las palabras de la búsqueda: la API
        REST de Shopify filtra por título exacto, no por palabras."""
        found = self._api.get("/products.json", params={"status": "active", "limit": 250})
        if found.status_code != 200:
            return _json(found.status_code, {"message": "Shopify no respondió."})
        currency = self._currency()
        products = [
            only_size(product_view(p, currency), size)
            for p in found.json().get("products") or []
            if matches(query, f"{p.get('title')} {p.get('product_type')} {p.get('tags')}")
        ]
        return _json(200, {"products": products})

    def _currency(self) -> str | None:
        shop = self._api.get("/shop.json")
        if shop.status_code != 200:
            return None
        currency = (shop.json().get("shop") or {}).get("currency")
        return str(currency) if currency else None

    def _purchase(self, ref: str) -> httpx.Response:
        """La compra de una conversación: el pedido cuyo atributo `ref` (lo deja el permalink en
        `note_attributes`) es el de la conversación, entre los de los últimos 60 días."""
        since = (datetime.now(UTC) - timedelta(days=60)).isoformat(timespec="seconds")
        found = self._api.get(
            "/orders.json", params={"status": "any", "limit": 250, "created_at_min": since}
        )
        if found.status_code != 200:
            return _json(found.status_code, {"message": "Shopify no respondió."})
        for order in found.json().get("orders") or []:
            notes = {
                str(n.get("name")): str(n.get("value")) for n in order.get("note_attributes") or []
            }
            if notes.get(REF_ATTRIBUTE) == ref and not order.get("cancelled_at"):
                return _json(
                    200,
                    {
                        "ref": ref,
                        "status": "completed",
                        "order_id": str(order.get("name", "")).lstrip("#"),
                        "total": float(order.get("total_price") or 0),
                        "currency": order.get("currency") or "",
                    },
                )
        return _json(404, {"code": "checkout_not_found", "message": "Todavía no hay compra."})

    def _checkout(
        self, items: list[dict[str, Any]], discount: float = 0, ref: str = ""
    ) -> httpx.Response:
        """El permalink de carrito de Shopify (`/cart/{variante}:{cantidad},...`): abre el
        checkout de la tienda con el carrito armado. No crea nada en Shopify ni cobra."""
        found = self._api.get("/products.json", params={"status": "active", "limit": 250})
        if found.status_code != 200:
            return _json(found.status_code, {"message": "Shopify no respondió."})
        currency = self._currency()
        lookup: dict[str, tuple[dict[str, Any], dict[str, Any], Any]] = {}
        for product in found.json().get("products") or []:
            view = product_view(product, currency)
            for raw, variant in zip(product.get("variants") or [], view["variants"], strict=True):
                lookup[variant["sku"]] = (view, variant, raw.get("id"))
        lines: list[dict[str, Any]] = []
        parts: list[str] = []
        for item in items:
            sku, quantity = str(item.get("sku", "")), int(item.get("quantity") or 1)
            if sku not in lookup:
                return _json(409, {"code": "unknown_sku", "message": f"No hay un producto {sku}."})
            view, variant, variant_id = lookup[sku]
            if variant["stock"] is not None and variant["stock"] < quantity:
                return _json(409, {"code": "out_of_stock", "message": f"{view['name']} sin stock."})
            parts.append(f"{variant_id}:{quantity}")
            lines.append(
                {
                    "sku": sku,
                    "name": view["name"],
                    "size": variant["size"],
                    "color": variant["color"],
                    "quantity": quantity,
                    "unit_price": variant["price"],
                }
            )
        host = str(self._api.base_url.host)
        total = sum(x["unit_price"] * x["quantity"] for x in lines)
        checkout: dict[str, Any] = {
            "url": f"https://{host}/cart/{','.join(parts)}",
            "items": lines,
            "total": round(total, 2),
            "currency": currency or "",
        }
        query: dict[str, str] = {}
        if discount:
            # El descuento de una oferta (decisión 061): un código de un solo uso que el
            # permalink aplica con `?discount=`.
            code = self._discount_code("OFERTA", discount)
            if isinstance(code, httpx.Response):
                return code
            query["discount"] = code
            checkout["total"] = round(total * (1 - discount / 100), 2)
            checkout.update({"discount_percent": discount, "discount_code": code})
        if ref:  # la conversación (decisión 063): queda en el pedido como atributo
            query[f"attributes[{REF_ATTRIBUTE}]"] = ref
        if query:
            checkout["url"] += "?" + urlencode(query)
        return _json(201, checkout)

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
        code = self._discount_code("GRACIAS", percent)
        if isinstance(code, httpx.Response):
            return code
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

    def _discount_code(self, prefix: str, percent: float) -> str | httpx.Response:
        """Un código de descuento de un solo uso: una regla de precio con su código."""
        code = f"{prefix}-{secrets.token_hex(3).upper()}"
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
        return code


def client(shop: str, token: str, upstream: httpx.BaseTransport | None = None) -> httpx.Client:
    """El cliente HTTP que usa el runtime de herramientas: habla la API canónica."""
    return httpx.Client(base_url=CANONICAL_BASE, transport=ShopifyTransport(shop, token, upstream))
