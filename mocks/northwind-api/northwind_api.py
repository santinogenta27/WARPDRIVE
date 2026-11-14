"""Simulated orders API for Northwind Outfitters (fictional demo store). Implements the
`openapi.json` in this folder.

Each `create_app()` starts from a clean copy of `data/orders.json`, so tests don't leak into
each other. Order ids are accepted as "NW-4521", "nw4521", "#4521" or "4521". Run locally:
`uvicorn northwind_api:app --app-dir mocks/northwind-api --port 8082`.
"""

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
OPENAPI_PATH = HERE / "openapi.json"
ORDERS_PATH = HERE / "data" / "orders.json"
CUSTOMERS_PATH = HERE / "data" / "customers.json"
NOT_SHIPPED = {"paid"}
_ORDER_ID = re.compile(r"^\s*#?\s*(?:nw)?[\s-]*(\d{3,6})\s*$", re.I)


class AddressChange(BaseModel):
    address: str = Field(min_length=5)


class RefundRequest(BaseModel):
    amount: float = Field(gt=0)


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

    return app


app = create_app()
