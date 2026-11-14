"""Simulated orders API for Northwind Outfitters (fictional demo store). Implements the
`openapi.json` in this folder.

Each `create_app()` starts from a clean copy of `data/orders.json`, so tests don't leak into
each other. Order ids are accepted as "NW-4521", "nw4521", "#4521" or "4521". Run locally:
`uvicorn northwind_api:app --app-dir mocks/northwind-api --port 8082`.
"""

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
OPENAPI_PATH = HERE / "openapi.json"
ORDERS_PATH = HERE / "data" / "orders.json"
CUSTOMERS_PATH = HERE / "data" / "customers.json"
PRODUCTS_PATH = HERE / "data" / "products.json"
NOT_SHIPPED = {"paid"}
COURTESY_DAYS = 60  # vigencia del cupón y del envío express gratis
_ORDER_ID = re.compile(r"^\s*#?\s*(?:nw)?[\s-]*(\d{3,6})\s*$", re.I)


class AddressChange(BaseModel):
    address: str = Field(min_length=5)


class RefundRequest(BaseModel):
    amount: float = Field(gt=0)


class ExchangeRequest(BaseModel):
    item_sku: str
    new_size: str | None = None
    new_color: str | None = None
    new_product: str | None = None


class CouponRequest(BaseModel):
    percent: int = Field(ge=1, le=100)


def canonical(order_id: str) -> str:
    """ "nw4521", "#4521" o "4521" -> "NW-4521"."""
    found = _ORDER_ID.match(order_id)
    return f"NW-{found.group(1)}" if found else order_id.strip().upper()


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_app(orders: list[dict[str, Any]] | None = None) -> FastAPI:
    data = orders if orders is not None else json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
    state: dict[str, dict[str, Any]] = {o["id"]: copy.deepcopy(o) for o in data}
    refunds: dict[str, dict[str, Any]] = {}  # by idempotency key
    cancels: set[str] = set()
    exchanges: dict[str, dict[str, Any]] = {}  # by idempotency key
    exchanged: set[str] = set()  # "order:sku": each item is exchanged once
    courtesies: dict[tuple[str, str], dict[str, Any]] = {}  # (kind, idempotency key)
    given_for: dict[str, str] = {}  # one courtesy per order: order id -> what it got
    products = {p["name"].lower(): p for p in json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))}

    app = FastAPI(title="Northwind Outfitters — Orders API (simulated)")
    app.openapi = lambda: json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))  # type: ignore[method-assign]
    customers = json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8"))

    def find(order_id: str) -> dict[str, Any] | None:
        return state.get(canonical(order_id))

    def missing(order_id: str) -> JSONResponse:
        return _error(404, "order_not_found", f"Order {order_id} doesn't exist")

    @app.get("/customers")
    def find_customers(email: str) -> dict[str, Any]:
        wanted = email.strip().lower()
        return {"customers": [c for c in customers if c["email"].lower() == wanted]}

    @app.get("/orders")
    def list_orders(customer_phone: str) -> dict[str, Any]:
        return {"orders": [o for o in state.values() if o["customer_phone"] == customer_phone]}

    @app.get("/orders/{order_id}", response_model=None)
    def get_order(order_id: str) -> dict[str, Any] | JSONResponse:
        order = find(order_id)
        return order if order is not None else missing(order_id)

    @app.patch("/orders/{order_id}/address", response_model=None)
    def change_address(order_id: str, body: AddressChange) -> dict[str, Any] | JSONResponse:
        order = find(order_id)
        if order is None:
            return missing(order_id)
        if order["status"] not in NOT_SHIPPED:
            return _error(409, "already_shipped", "The order has already shipped")
        order["shipping_address"] = body.address
        return order

    @app.post("/orders/{order_id}/cancel", response_model=None)
    def cancel_order(
        order_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
    ) -> dict[str, Any] | JSONResponse:
        order = find(order_id)
        if order is None:
            return missing(order_id)
        if idempotency_key is not None and idempotency_key in cancels:
            return order
        if order["status"] not in NOT_SHIPPED:
            return _error(409, "already_shipped", "The order has already shipped")
        order["status"] = "cancelled"
        if idempotency_key is not None:
            cancels.add(idempotency_key)
        return order

    @app.post("/orders/{order_id}/refunds", status_code=201, response_model=None)
    def create_refund(
        order_id: str,
        body: RefundRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any] | JSONResponse:
        order = find(order_id)
        if order is None:
            return missing(order_id)
        if idempotency_key is not None and idempotency_key in refunds:
            return JSONResponse(status_code=201, content=refunds[idempotency_key])
        if order["status"] != "delivered":
            return _error(409, "not_delivered", "Only delivered orders can be refunded")
        if body.amount > order["total"]:
            return _error(409, "amount_too_high", "The amount is over the order total")
        refund = {
            "id": f"R-{order['id']}-{len(refunds) + 1}",
            "order_id": order["id"],
            "amount": body.amount,
            "currency": "USD",
            "status": "requested",
            "created_at": _now(),
        }
        if idempotency_key is not None:
            refunds[idempotency_key] = refund
        return JSONResponse(status_code=201, content=refund)

    @app.post("/orders/{order_id}/exchanges", status_code=201, response_model=None)
    def create_exchange(
        order_id: str,
        body: ExchangeRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        order = find(order_id)
        if order is None:
            return missing(order_id)
        if idempotency_key is not None and idempotency_key in exchanges:
            return JSONResponse(status_code=201, content=exchanges[idempotency_key])
        if order["status"] != "delivered":
            return _error(409, "not_delivered", "Only delivered orders can be exchanged")
        item = next((i for i in order["items"] if i["sku"] == body.item_sku.strip()), None)
        if item is None:
            return _error(409, "item_not_in_order", f"{body.item_sku} isn't in the order")
        if f"{order['id']}:{item['sku']}" in exchanged:
            return _error(409, "already_exchanged", "Each item can be exchanged only once")
        product = products.get((body.new_product or item["name"]).strip().lower())
        if product is None:
            return _error(409, "unknown_product", f"We don't sell {body.new_product}")
        size = (body.new_size or item["size"]).strip().upper()
        color = (body.new_color or item["color"]).strip().title()
        sizes = {s.upper(): s for s in product["sizes"]}
        if size not in sizes:
            sizes_list = ", ".join(product["sizes"])
            return _error(409, "size_not_available", f"{product['name']} comes in {sizes_list}")
        if color not in product["colors"]:
            colors_list = ", ".join(product["colors"])
            return _error(409, "color_not_available", f"{product['name']} comes in {colors_list}")
        new = {"name": product["name"], "size": sizes[size], "color": color}
        if new == {k: item[k] for k in ("name", "size", "color")}:
            return _error(409, "same_item", "The new item is the same as the one in the order")
        exchange = {
            "id": f"EX-{order['id']}-{len(exchanges) + 1}",
            "order_id": order["id"],
            "status": "requested",
            "from_item": {k: item[k] for k in ("sku", "name", "size", "color")},
            "to_item": {**new, "unit_price": product["price"]},
            # Positiva: el cliente paga la diferencia; negativa: se le devuelve.
            "price_difference": round(product["price"] - item["unit_price"], 2),
            "currency": "USD",
            "return_label": "emailed",
            "new_item_ships": "when the carrier scans the return",
            "created_at": _now(),
        }
        exchanged.add(f"{order['id']}:{item['sku']}")
        if idempotency_key is not None:
            exchanges[idempotency_key] = exchange
        return JSONResponse(status_code=201, content=exchange)

    def courtesy(order: dict[str, Any], kind: str, key: str | None) -> JSONResponse | None:
        """Una cortesía por pedido (cupón o envío express): la repetida con la misma clave
        devuelve la misma, por tipo (el cupón y el envío pueden llegar con la misma clave);
        otra para el mismo pedido es un conflicto."""
        if key is not None and (kind, key) in courtesies:
            return JSONResponse(status_code=201, content=courtesies[(kind, key)])
        given = given_for.get(order["id"])
        if given is not None:
            return _error(
                409, "courtesy_already_given", f"Order {order['id']} already got a {given}"
            )
        return None

    def give(order: dict[str, Any], offer: dict[str, Any], key: str | None) -> JSONResponse:
        given_for[order["id"]] = offer["kind"].replace("_", " ")
        if key is not None:
            courtesies[(offer["kind"], key)] = offer
        return JSONResponse(status_code=201, content=offer)

    def expires() -> str:
        return (datetime.now(UTC).date() + timedelta(days=COURTESY_DAYS)).isoformat()

    @app.post("/orders/{order_id}/coupons", status_code=201, response_model=None)
    def create_coupon(
        order_id: str,
        body: CouponRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        order = find(order_id)
        if order is None:
            return missing(order_id)
        repeated = courtesy(order, "coupon", idempotency_key)
        if repeated is not None:
            return repeated
        digits = order["id"].removeprefix("NW-")
        offer = {
            "code": f"SORRY{body.percent}-{digits}",
            "kind": "coupon",
            "order_id": order["id"],
            "percent": body.percent,
            "applies_to": "next_order",
            "valid_days": COURTESY_DAYS,
            "expires_on": expires(),
        }
        return give(order, offer, idempotency_key)

    @app.post("/orders/{order_id}/express-shipping", status_code=201, response_model=None)
    def create_express_shipping(
        order_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        order = find(order_id)
        if order is None:
            return missing(order_id)
        repeated = courtesy(order, "free_express_shipping", idempotency_key)
        if repeated is not None:
            return repeated
        offer = {
            "code": f"EXPRESS-{order['id'].removeprefix('NW-')}",
            "kind": "free_express_shipping",
            "order_id": order["id"],
            "applies_to": "next_order",
            "valid_days": COURTESY_DAYS,
            "expires_on": expires(),
        }
        return give(order, offer, idempotency_key)

    return app


app = create_app()
