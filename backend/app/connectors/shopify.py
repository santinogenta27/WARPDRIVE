"""Conector de Shopify (decisiones 057 y 067): las herramientas de una tienda sin programar.

El agente y el guardián hablan con una **API canónica de tienda** (la misma de la demo de
Northwind: `GET /orders/{order_id}`, `POST /orders/{order_id}/refunds`, cupones, cancelación,
búsqueda de clientes por email). Este módulo es un transporte de httpx que traduce cada pedido
canónico a la **Admin GraphQL API** de Shopify (la REST es legacy) y la respuesta de vuelta. Así
las herramientas se generan del OpenAPI canónico, con los mismos controles (identidad, dueño,
montos, aprobación), y el guardián no cambia.

* **Pedido:** el número que ve el cliente (`1001` o `#1001`), buscado por `name`, con su envío
  y el seguimiento.
* **Dueño:** el teléfono del cliente o, si no tiene, su id de Shopify: el mismo valor sale en la
  búsqueda por email y en el pedido, así el control de dueño compara lo mismo.
* **Reembolso:** `refundCreate` sobre la transacción cobrada, por el monto pedido, con la clave
  de idempotencia del pedido canónico.
* **Cupón:** `discountCodeBasicCreate`, un código de un solo uso.
* **Cancelación:** `orderCancel`, solo si el pedido no se despachó.

Cada consulta tiene nombre (`query Pedido`): en los registros de Shopify se ve qué hizo el
asistente. Los errores de Shopify (token vencido, permiso que falta) vuelven con un mensaje
claro para el panel.
"""

import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.connectors import shopify_auth

API_VERSION = "2026-10"
CANONICAL_BASE = "https://tienda.local"
SHOP = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
REF_ATTRIBUTE = "asistente_ref"  # atributo del carrito con la conversación (decisión 063)
DIAGNOSE_PATH = "/_diagnostico"  # la prueba de conexión del panel (no es parte de la API)
SHIPPED = {"FULFILLED", "PARTIALLY_FULFILLED", "IN_PROGRESS", "ON_HOLD", "SCHEDULED"}

ORDER_FIELDS = """id name createdAt cancelledAt displayFinancialStatus displayFulfillmentStatus
currencyCode totalPriceSet { shopMoney { amount currencyCode } }
customer { id phone firstName } shippingAddress { address1 city province }
lineItems(first: 50) { nodes { sku title variantTitle quantity
  originalUnitPriceSet { shopMoney { amount } } } }
fulfillments(first: 10) { createdAt displayStatus trackingInfo(first: 1) { company number url } }
customAttributes { key value }"""

Q_ORDER = (
    "query Pedido($q: String!) { orders(first: 5, query: $q) { nodes { " + ORDER_FIELDS + " } } }"
)
Q_CUSTOMERS = (
    "query Clientes($q: String!) { customers(first: 5, query: $q) { nodes { id email phone } } }"
)
Q_PRODUCTS = """query Productos { shop { currencyCode }
products(first: 100, query: "status:active") { nodes { id title productType tags description
  options { name } variants(first: 100) { nodes { id sku title price inventoryQuantity
    inventoryItem { tracked } selectedOptions { name value } } } } } }"""
Q_PURCHASES = """query Compras($q: String!) {
orders(first: 100, query: $q, sortKey: CREATED_AT, reverse: true) { nodes { name cancelledAt
  currencyCode totalPriceSet { shopMoney { amount currencyCode } }
  customAttributes { key value } } } }"""
Q_TRANSACTIONS = (
    "query Cobros($id: ID!) { order(id: $id) { id transactions(first: 20) "
    "{ id kind status gateway } } }"
)
Q_SCOPES = "query Permisos { shop { name } currentAppInstallation { accessScopes { handle } } }"
M_REFUND = """mutation Reembolso($input: RefundInput!, $key: String!) {
refundCreate(input: $input) @idempotent(key: $key) {
  refund { id createdAt totalRefundedSet { shopMoney { amount currencyCode } } }
  userErrors { field message } } }"""
M_DISCOUNT = """mutation Descuento($d: DiscountCodeBasicInput!) {
discountCodeBasicCreate(basicCodeDiscount: $d) { codeDiscountNode { id }
  userErrors { field message } } }"""
M_CANCEL = """mutation Cancelar($id: ID!, $note: String) {
orderCancel(orderId: $id, reason: CUSTOMER, restock: true, notifyCustomer: true,
  refundMethod: { originalPaymentMethodsRefund: true }, staffNote: $note) {
  job { id done } orderCancelUserErrors { field message code } } }"""


class ShopifyError(Exception):
    """Una respuesta de Shopify que no es un dato: vuelve como error de la API canónica."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def response(self) -> httpx.Response:
        return _json(self.status, {"code": self.code, "message": self.message})


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


def _money(value: Any) -> float:
    return float(((value or {}).get("shopMoney") or {}).get("amount") or 0)


def _number(gid: Any) -> str:
    """`gid://shopify/ProductVariant/808950810` → `808950810`."""
    return str(gid or "").rsplit("/", 1)[-1]


def _quote(value: str) -> str:
    """Un valor dentro de la sintaxis de búsqueda de Shopify, sin poder cerrar las comillas."""
    return '"' + value.replace("\\", "").replace('"', "") + '"'


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
    variants: list[dict[str, Any]] = []
    for variant in (product.get("variants") or {}).get("nodes") or []:
        by_name = {
            str(o.get("name", "")).strip().lower(): o.get("value")
            for o in variant.get("selectedOptions") or []
        }
        size = next((v for n, v in by_name.items() if n in SIZE_OPTIONS), None)
        color = next((v for n, v in by_name.items() if n in COLOR_OPTIONS), None)
        tracked = bool((variant.get("inventoryItem") or {}).get("tracked"))
        variants.append(
            {
                "sku": str(variant.get("sku") or _number(variant.get("id"))),
                "size": str(size or variant.get("title") or ""),
                "color": str(color or ""),
                "stock": max(int(variant.get("inventoryQuantity") or 0), 0) if tracked else None,
                "price": float(variant.get("price") or 0),
            }
        )
    prices = [v["price"] for v in variants]
    return {
        "code": _number(product.get("id")),
        "name": product.get("title"),
        "category": product.get("productType") or "",
        "description": str(product.get("description") or "").strip(),
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


def _status(order: dict[str, Any]) -> str:
    financial = str(order.get("displayFinancialStatus") or "")
    fulfillment = str(order.get("displayFulfillmentStatus") or "")
    fulfillments = order.get("fulfillments") or []
    if order.get("cancelledAt"):
        return "cancelled"
    if financial == "REFUNDED":
        return "refunded"
    if fulfillments and fulfillments[-1].get("displayStatus") == "DELIVERED":
        return "delivered"
    if fulfillment in SHIPPED or fulfillments:
        return "shipped"
    if financial == "PENDING":
        return "pending"
    return "paid" if financial else "pending"


def order_view(order: dict[str, Any]) -> dict[str, Any]:
    """Un pedido de Shopify con la forma del pedido canónico (el Order del OpenAPI)."""
    fulfillments = order.get("fulfillments") or []
    last = fulfillments[-1] if fulfillments else {}
    tracking = (last.get("trackingInfo") or [{}])[0] if last else {}
    address = order.get("shippingAddress") or {}
    customer = order.get("customer") or {}
    return {
        "id": str(order.get("name", "")).lstrip("#"),
        "customer_phone": _owner(customer),
        "customer_name": customer.get("firstName"),
        "status": _status(order),
        "items": [
            {
                "sku": item.get("sku"),
                "name": item.get("title"),
                "size": item.get("variantTitle"),
                "quantity": item.get("quantity"),
                "unit_price": _money(item.get("originalUnitPriceSet")),
            }
            for item in (order.get("lineItems") or {}).get("nodes") or []
        ],
        "total": _money(order.get("totalPriceSet")),
        "currency": order.get("currencyCode"),
        "shipping_address": ", ".join(
            str(address[k]) for k in ("address1", "city", "province") if address.get(k)
        ),
        "created_at": order.get("createdAt"),
        "shipped_at": last.get("createdAt"),
        "carrier": tracking.get("company"),
        "tracking_number": tracking.get("number"),
    }


def _graphql_errors(errors: Any) -> ShopifyError:
    """El error de GraphQL con un mensaje para quien configura la tienda."""
    raw = errors if isinstance(errors, list) else [errors]
    items: list[dict[str, Any]] = [e if isinstance(e, dict) else {"message": str(e)} for e in raw]
    for item in items:
        extensions: dict[str, Any] = item.get("extensions") or {}
        if extensions.get("code") == "ACCESS_DENIED":
            needed = re.findall(
                r"`(\w+)`", f"{extensions.get('requiredAccess', '')} {item.get('message', '')}"
            )
            scope = needed[0] if needed else "un permiso"
            return ShopifyError(
                403,
                "missing_scope",
                f"A la app de Shopify le falta el permiso {scope}. Agregalo en el Dev Dashboard, "
                "publicá una versión nueva de la app y volvé a conectar la tienda.",
            )
        if extensions.get("code") == "THROTTLED":
            return ShopifyError(429, "throttled", "Shopify pidió esperar: probá en un momento.")
    message = "; ".join(str(i.get("message")) for i in items)[:300]
    return ShopifyError(502, "shopify_error", f"Shopify respondió con un error: {message}")


class ShopifyTransport(httpx.BaseTransport):
    def __init__(self, shop: str, secret: str, upstream: httpx.BaseTransport | None = None) -> None:
        creds = shopify_auth.parse(secret)
        self.shop = clean_shop(shop)
        self.creds = creds
        self._api = httpx.Client(
            base_url=f"https://{self.shop}/admin/api/{API_VERSION}",
            headers={"X-Shopify-Access-Token": creds.access_token},
            transport=upstream,
            timeout=15.0,
            follow_redirects=False,
        )

    # -- GraphQL ---------------------------------------------------------------------------
    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._api.post(
            "/graphql.json", json={"query": query, "variables": variables or {}}
        )
        if response.status_code == 401:
            raise ShopifyError(
                401,
                "token_invalid",
                "Shopify rechazó el token: venció o se revocó. Volvé a conectar la tienda en "
                "Configuración → Sistema.",
            )
        if response.status_code == 402:
            raise ShopifyError(402, "shop_frozen", "La tienda de Shopify está pausada o sin pago.")
        if response.status_code in (403, 404):
            raise ShopifyError(
                403, "forbidden", "Shopify no deja usar la API de esta tienda con esta app."
            )
        if response.status_code == 429:
            raise ShopifyError(429, "throttled", "Shopify pidió esperar: probá en un momento.")
        if response.status_code >= 400:
            raise ShopifyError(502, "shopify_error", f"Shopify respondió {response.status_code}.")
        body = response.json()
        if body.get("errors"):
            raise _graphql_errors(body["errors"])
        data: dict[str, Any] = body.get("data") or {}
        return data

    @staticmethod
    def _user_errors(errors: list[dict[str, Any]] | None, what: str) -> None:
        if errors:
            detail = "; ".join(str(e.get("message")) for e in errors)[:300]
            raise ShopifyError(409, "rejected", f"Shopify rechazó {what}: {detail}")

    # -- traducción ------------------------------------------------------------------------
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._route(request)
        except ShopifyError as error:
            return error.response()

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        body = json.loads(request.content) if request.content else {}
        key = request.headers.get("Idempotency-Key") or uuid.uuid4().hex
        if request.method == "GET" and path == DIAGNOSE_PATH:
            return self._diagnose()
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
            return self._refund(order, float(body.get("amount") or 0), key)
        if request.method == "POST" and action == "coupons":
            return self._coupon(order, float(body.get("percent") or 0))
        if request.method == "POST" and action == "cancel":
            return self._cancel(order)
        return _json(404, {"code": "not_found", "message": "Operación no disponible."})

    def _diagnose(self) -> httpx.Response:
        """La prueba de conexión: la tienda responde, acepta el token y la app tiene los
        permisos que usa el conector."""
        data = self._gql(Q_SCOPES)
        granted = [
            str(s.get("handle"))
            for s in (data.get("currentAppInstallation") or {}).get("accessScopes") or []
        ]
        missing = shopify_auth.missing_scopes(granted)
        if missing:
            return _json(
                403,
                {
                    "code": "missing_scope",
                    "message": "A la app de Shopify le faltan permisos: "
                    + ", ".join(missing)
                    + ". Agregalos en el Dev Dashboard, publicá una versión nueva y volvé a "
                    "conectar la tienda.",
                    "missing": missing,
                },
            )
        shop = (data.get("shop") or {}).get("name")
        return _json(200, {"shop": shop, "scopes": granted, "method": self.creds.method})

    def _customers(self, email: str) -> httpx.Response:
        data = self._gql(Q_CUSTOMERS, {"q": f"email:{_quote(email)}"})
        customers = [
            {"id": str(c.get("id")), "email": c.get("email"), "phone": _owner(c)}
            for c in (data.get("customers") or {}).get("nodes") or []
            if str(c.get("email", "")).lower() == email.lower()
        ]
        return _json(200, {"customers": customers})

    def _catalog(self) -> tuple[list[dict[str, Any]], str | None]:
        data = self._gql(Q_PRODUCTS)
        currency = (data.get("shop") or {}).get("currencyCode")
        return (data.get("products") or {}).get("nodes") or [], currency

    def _products(self, query: str, size: str | None) -> httpx.Response:
        """Los productos activos (hasta 100) filtrados por las palabras de la búsqueda."""
        products, currency = self._catalog()
        found = [
            only_size(product_view(p, currency), size)
            for p in products
            if matches(
                query, f"{p.get('title')} {p.get('productType')} {' '.join(p.get('tags') or [])}"
            )
        ]
        return _json(200, {"products": found})

    def _purchase(self, ref: str) -> httpx.Response:
        """La compra de una conversación: el pedido cuyo atributo `ref` (lo deja el permalink en
        los atributos del carrito) es el de la conversación, entre los de los últimos 60 días."""
        since = (datetime.now(UTC) - timedelta(days=60)).date().isoformat()
        data = self._gql(Q_PURCHASES, {"q": f"created_at:>={since}"})
        for order in (data.get("orders") or {}).get("nodes") or []:
            attributes = {
                str(a.get("key")): str(a.get("value")) for a in order.get("customAttributes") or []
            }
            if attributes.get(REF_ATTRIBUTE) == ref and not order.get("cancelledAt"):
                return _json(
                    200,
                    {
                        "ref": ref,
                        "status": "completed",
                        "order_id": str(order.get("name", "")).lstrip("#"),
                        "total": _money(order.get("totalPriceSet")),
                        "currency": order.get("currencyCode") or "",
                    },
                )
        return _json(404, {"code": "checkout_not_found", "message": "Todavía no hay compra."})

    def _checkout(
        self, items: list[dict[str, Any]], discount: float = 0, ref: str = ""
    ) -> httpx.Response:
        """El permalink de carrito de Shopify (`/cart/{variante}:{cantidad},...`): abre el
        checkout de la tienda con el carrito armado. No crea nada en Shopify ni cobra."""
        products, currency = self._catalog()
        lookup: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {}
        for product in products:
            view = product_view(product, currency)
            raw = (product.get("variants") or {}).get("nodes") or []
            for node, variant in zip(raw, view["variants"], strict=True):
                lookup[variant["sku"]] = (view, variant, _number(node.get("id")))
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
        total = sum(x["unit_price"] * x["quantity"] for x in lines)
        checkout: dict[str, Any] = {
            "url": f"https://{self.shop}/cart/{','.join(parts)}",
            "items": lines,
            "total": round(total, 2),
            "currency": currency or "",
        }
        query: dict[str, str] = {}
        if discount:
            # El descuento de una oferta (decisión 061): un código de un solo uso que el
            # permalink aplica con `?discount=`.
            code = self._discount_code("OFERTA", discount)
            query["discount"] = code
            checkout["total"] = round(total * (1 - discount / 100), 2)
            checkout.update({"discount_percent": discount, "discount_code": code})
        if ref:  # la conversación (decisión 063): queda en el pedido como atributo
            query[f"attributes[{REF_ATTRIBUTE}]"] = ref
        if query:
            checkout["url"] += "?" + urlencode(query)
        return _json(201, checkout)

    def _order(self, number: str) -> dict[str, Any] | None:
        clean = re.sub(r"[^\w-]", "", number.lstrip("#"))
        if not clean:
            return None
        data = self._gql(Q_ORDER, {"q": f"name:{clean}"})
        orders = [
            o for o in (data.get("orders") or {}).get("nodes") or [] if o.get("name") == f"#{clean}"
        ]
        return orders[0] if len(orders) == 1 else None

    def _refund(self, order: dict[str, Any], amount: float, key: str) -> httpx.Response:
        found = self._gql(Q_TRANSACTIONS, {"id": order["id"]})
        paid = [
            t
            for t in (found.get("order") or {}).get("transactions") or []
            if t.get("kind") in ("SALE", "CAPTURE") and t.get("status") == "SUCCESS"
        ]
        if not paid:
            return _json(409, {"code": "not_paid", "message": "El pedido no tiene un cobro."})
        refund_input = {
            "orderId": order["id"],
            "notify": True,
            "note": "Reembolso aprobado desde el asistente",
            "transactions": [
                {
                    "orderId": order["id"],
                    "parentId": paid[0]["id"],
                    "kind": "REFUND",
                    "gateway": paid[0].get("gateway"),
                    "amount": f"{amount:.2f}",
                }
            ],
        }
        done = self._gql(M_REFUND, {"input": refund_input, "key": key})
        payload = done.get("refundCreate") or {}
        self._user_errors(payload.get("userErrors"), "el reembolso")
        created = payload.get("refund") or {}
        return _json(
            201,
            {
                "id": _number(created.get("id")),
                "order_id": str(order.get("name", "")).lstrip("#"),
                "amount": amount,
                "currency": order.get("currencyCode"),
                "status": "processed",
                "created_at": created.get("createdAt"),
            },
        )

    def _coupon(self, order: dict[str, Any], percent: float) -> httpx.Response:
        code = self._discount_code("GRACIAS", percent)
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

    def _discount_code(self, prefix: str, percent: float) -> str:
        """Un código de descuento de un solo uso, para todos los productos."""
        code = f"{prefix}-{secrets.token_hex(3).upper()}"
        discount = {
            "title": code,
            "code": code,
            "startsAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "context": {"all": "ALL"},
            "customerGets": {
                "value": {"percentage": round(percent / 100, 4)},
                "items": {"all": True},
            },
            "appliesOncePerCustomer": True,
            "usageLimit": 1,
        }
        done = self._gql(M_DISCOUNT, {"d": discount})
        payload = done.get("discountCodeBasicCreate") or {}
        self._user_errors(payload.get("userErrors"), "el cupón")
        return code

    def _cancel(self, order: dict[str, Any]) -> httpx.Response:
        """Cancela, devuelve el pago y repone el stock, solo antes del despacho. Shopify cancela
        en segundo plano (un job): el pedido vuelve como cancelado."""
        view = order_view(order)
        if view["status"] == "cancelled":
            return _json(200, view)
        if view["status"] not in ("paid", "pending"):
            return _json(409, {"code": "already_shipped", "message": "El pedido ya se despachó."})
        done = self._gql(M_CANCEL, {"id": order["id"], "note": "Cancelado desde el asistente"})
        payload = done.get("orderCancel") or {}
        self._user_errors(payload.get("orderCancelUserErrors"), "la cancelación")
        return _json(200, view | {"status": "cancelled"})


def client(shop: str, secret: str, upstream: httpx.BaseTransport | None = None) -> httpx.Client:
    """El cliente HTTP que usa el runtime de herramientas: habla la API canónica. `secret` es el
    token pegado o las credenciales guardadas (JSON, decisión 067)."""
    return httpx.Client(base_url=CANONICAL_BASE, transport=ShopifyTransport(shop, secret, upstream))
