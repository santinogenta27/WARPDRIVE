"""Una tienda de Shopify falsa para los tests (decisión 067): la Admin GraphQL API y el
endpoint de tokens, con respuestas de la forma de los ejemplos de la documentación de Shopify
(`2026-10`).

Cada consulta del conector tiene nombre (`query Pedido`, `mutation Reembolso`…): la tienda
responde según ese nombre y guarda cada llamada para que el test la revise.
"""

import json
import re
from typing import Any
from urllib.parse import parse_qs

import httpx

SHOP = "mitienda.myshopify.com"
GRAPHQL = "/admin/api/2026-10/graphql.json"
TOKEN_PATH = "/admin/oauth/access_token"
ALL_SCOPES = [
    "read_customers",
    "write_orders",
    "read_products",
    "read_inventory",
    "write_discounts",
]

# Un pedido como lo devuelve `orders(query: "name:1001")`: pagado, despachado con seguimiento.
ORDER: dict[str, Any] = {
    "id": "gid://shopify/Order/450789469",
    "name": "#1001",
    "createdAt": "2026-11-10T13:00:00Z",
    "cancelledAt": None,
    "displayFinancialStatus": "PAID",
    "displayFulfillmentStatus": "FULFILLED",
    "currencyCode": "ARS",
    "totalPriceSet": {"shopMoney": {"amount": "45000.0", "currencyCode": "ARS"}},
    "customer": {"id": "gid://shopify/Customer/207119551", "phone": None, "firstName": "Ana"},
    "shippingAddress": {"address1": "Corrientes 1234", "city": "CABA", "province": "Buenos Aires"},
    "lineItems": {
        "nodes": [
            {
                "sku": "CP-1",
                "title": "Campera",
                "variantTitle": "M / Negro",
                "quantity": 1,
                "originalUnitPriceSet": {"shopMoney": {"amount": "45000.0"}},
            }
        ]
    },
    "fulfillments": [
        {
            "createdAt": "2026-11-11T12:00:00Z",
            "displayStatus": "IN_TRANSIT",
            "trackingInfo": [
                {"company": "Andreani", "number": "AR123", "url": "https://andreani.com/AR123"}
            ],
        }
    ],
    "customAttributes": [],
}

# Un pedido pagado y sin despachar: se puede cancelar.
UNSHIPPED: dict[str, Any] = {
    **ORDER,
    "id": "gid://shopify/Order/450789470",
    "name": "#1002",
    "displayFulfillmentStatus": "UNFULFILLED",
    "fulfillments": [],
}

CUSTOMER = {"id": "gid://shopify/Customer/207119551", "email": "ana@example.com", "phone": None}

TRANSACTIONS = [
    {
        "id": "gid://shopify/OrderTransaction/389404469",
        "kind": "SALE",
        "status": "SUCCESS",
        "gateway": "mercado_pago",
    }
]


def product(
    title: str = "Campera Impermeable", variants: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Un producto de `products(query: "status:active")` con sus variantes."""
    return {
        "id": "gid://shopify/Product/632910392",
        "title": title,
        "productType": "Camperas",
        "tags": ["lluvia", "outdoor"],
        "description": "Campera liviana para lluvia.",
        "options": [{"name": "Talle"}, {"name": "Color"}],
        "variants": {"nodes": variants or []},
    }


def variant(
    variant_id: int, sku: str, size: str, stock: int | None, *, color: str | None = None
) -> dict[str, Any]:
    options = [{"name": "Talle", "value": size}]
    if color:
        options.append({"name": "Color", "value": color})
    return {
        "id": f"gid://shopify/ProductVariant/{variant_id}",
        "sku": sku,
        "title": " / ".join(o["value"] for o in options),
        "price": "85000.00",
        "inventoryQuantity": stock or 0,
        "inventoryItem": {"tracked": stock is not None},
        "selectedOptions": options,
    }


class FakeShopify:
    """La tienda falsa. `token` es el que acepta la API; `scopes`, los permisos de la app."""

    def __init__(
        self,
        *,
        token: str = "shpat_123",
        scopes: list[str] | None = None,
        orders: list[dict[str, Any]] | None = None,
        products: list[dict[str, Any]] | None = None,
        currency: str = "ARS",
    ) -> None:
        self.token = token
        self.scopes = list(ALL_SCOPES if scopes is None else scopes)
        self.orders = [ORDER, UNSHIPPED] if orders is None else orders
        self.products = products or []
        self.currency = currency
        self.calls: list[dict[str, Any]] = []
        self.token_requests: list[dict[str, str]] = []
        self.issued = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def operations(self) -> list[str]:
        return [c["operation"] for c in self.calls]

    def call(self, operation: str) -> dict[str, Any]:
        return next(c for c in self.calls if c["operation"] == operation)

    # -- HTTP ----------------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == SHOP
        if request.url.path == TOKEN_PATH:
            return self._token(request)
        assert request.url.path == GRAPHQL, f"solo GraphQL, no {request.url.path}"
        assert request.method == "POST"
        if request.headers.get("X-Shopify-Access-Token") != self.token:
            # La respuesta de Shopify a un token inválido o vencido.
            return httpx.Response(
                401, json={"errors": "[API] Invalid API key or access token (unrecognized login)"}
            )
        body = json.loads(request.content)
        found = re.match(r"\s*(query|mutation)\s+(\w+)", body["query"])
        assert found, "cada consulta del conector tiene nombre"
        operation = found.group(2)
        self.calls.append(
            {"operation": operation, "query": body["query"], "variables": body.get("variables")}
        )
        return getattr(self, f"_{operation.lower()}")(body.get("variables") or {})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        self.issued += 1
        token = f"shpat_nuevo{self.issued}"
        self.token = token
        if form.get("grant_type") == "client_credentials":
            if form.get("client_secret") != "shpss_secreto":
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_client",
                        "error_description": "Client authentication failed",
                    },
                )
            return httpx.Response(
                200,
                json={"access_token": token, "scope": ",".join(self.scopes), "expires_in": 86399},
            )
        if form.get("grant_type") == "refresh_token" or "code" in form:
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "scope": ",".join(self.scopes),
                    "expires_in": 3600,
                    "refresh_token": f"shprt_{self.issued}",
                    "refresh_token_expires_in": 7776000,
                },
            )
        return httpx.Response(400, json={"error": "invalid_request"})

    def _denied(self, scope: str) -> httpx.Response | None:
        granted = set(self.scopes)
        if scope in granted or scope.replace("read_", "write_") in granted:
            return None
        # Falta un permiso: HTTP 200 con el error en `errors`.
        return httpx.Response(
            200,
            json={
                "data": None,
                "errors": [
                    {
                        "message": f"Access denied for orders field. Required access: `{scope}`.",
                        "extensions": {"code": "ACCESS_DENIED", "requiredAccess": f"`{scope}`"},
                    }
                ],
            },
        )

    @staticmethod
    def _data(data: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"data": data, "extensions": {"cost": {}}})

    # -- operaciones ---------------------------------------------------------------------------
    def _permisos(self, _: dict[str, Any]) -> httpx.Response:
        return self._data(
            {
                "shop": {
                    "name": "Mi Tienda",
                    "myshopifyDomain": SHOP,
                    "primaryDomain": {"host": "www.mitienda.com.ar"},
                },
                "currentAppInstallation": {"accessScopes": [{"handle": s} for s in self.scopes]},
            }
        )

    def _clientes(self, variables: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("read_customers"):
            return denied
        email = variables["q"].removeprefix("email:").strip('"')
        found = [CUSTOMER] if email == CUSTOMER["email"] else []
        return self._data({"customers": {"nodes": found}})

    def _pedido(self, variables: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("read_orders"):
            return denied
        number = variables["q"].removeprefix("name:")
        found = [o for o in self.orders if o["name"] == f"#{number}"]
        return self._data({"orders": {"nodes": found}})

    def _compras(self, variables: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("read_orders"):
            return denied
        return self._data({"orders": {"nodes": self.orders}})

    def _cobros(self, variables: dict[str, Any]) -> httpx.Response:
        return self._data({"order": {"id": variables["id"], "transactions": TRANSACTIONS}})

    def _productos(self, _: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("read_products"):
            return denied
        return self._data(
            {"shop": {"currencyCode": self.currency}, "products": {"nodes": self.products}}
        )

    def _reembolso(self, variables: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("write_orders"):
            return denied
        amount = variables["input"]["transactions"][0]["amount"]
        return self._data(
            {
                "refundCreate": {
                    "refund": {
                        "id": "gid://shopify/Refund/509562969",
                        "createdAt": "2026-11-18T15:00:00Z",
                        "totalRefundedSet": {
                            "shopMoney": {"amount": amount, "currencyCode": "ARS"}
                        },
                    },
                    "userErrors": [],
                }
            }
        )

    def _descuento(self, _: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("write_discounts"):
            return denied
        return self._data(
            {
                "discountCodeBasicCreate": {
                    "codeDiscountNode": {"id": "gid://shopify/DiscountCodeNode/1057371198"},
                    "userErrors": [],
                }
            }
        )

    def _cancelar(self, _: dict[str, Any]) -> httpx.Response:
        if denied := self._denied("write_orders"):
            return denied
        return self._data(
            {
                "orderCancel": {
                    "job": {
                        "id": "gid://shopify/Job/1d8bd3f9-0c4e-4d21-a3e5-2f0a1b6c7d8e",
                        "done": False,
                    },
                    "orderCancelUserErrors": [],
                }
            }
        )
