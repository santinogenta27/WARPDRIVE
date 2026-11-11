"""API simulada de pedidos de Acme Store (ficticia). Cumple `openapi.json` de esta carpeta.

Cada `create_app()` arranca con una copia limpia de `data/orders.json`, así los tests no se
contaminan entre sí. Correr local: `uvicorn acme_api:app --app-dir mocks/acme-api --port 8081`.
"""

import copy
import json
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


class AddressChange(BaseModel):
    address: str = Field(min_length=5)


class RefundRequest(BaseModel):
    amount: float = Field(gt=0)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_app(orders: list[dict[str, Any]] | None = None) -> FastAPI:
    data = orders if orders is not None else json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
    state: dict[str, dict[str, Any]] = {o["id"]: copy.deepcopy(o) for o in data}
    refunds: dict[str, dict[str, Any]] = {}  # por clave de idempotencia
    cancels: set[str] = set()  # claves de cancelación ya aplicadas

    app = FastAPI(title="Acme Store — API de pedidos (simulada)")
    app.openapi = lambda: json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))  # type: ignore[method-assign]

    customers = json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8"))

    @app.get("/customers")
    def find_customers(email: str) -> dict[str, Any]:
        wanted = email.strip().lower()
        return {"customers": [c for c in customers if c["email"].lower() == wanted]}

    @app.get("/orders")
    def list_orders(customer_phone: str) -> dict[str, Any]:
        return {"orders": [o for o in state.values() if o["customer_phone"] == customer_phone]}

    @app.get("/orders/{order_id}", response_model=None)
    def get_order(order_id: str) -> dict[str, Any] | JSONResponse:
        order = state.get(order_id)
        if order is None:
            return _error(404, "order_not_found", f"No existe el pedido {order_id}")
        return order

    @app.patch("/orders/{order_id}/address", response_model=None)
    def change_address(order_id: str, body: AddressChange) -> dict[str, Any] | JSONResponse:
        order = state.get(order_id)
        if order is None:
            return _error(404, "order_not_found", f"No existe el pedido {order_id}")
        if order["status"] not in NOT_SHIPPED:
            return _error(409, "already_shipped", "El pedido ya fue despachado")
        order["shipping_address"] = body.address
        return order

    @app.post("/orders/{order_id}/cancel", response_model=None)
    def cancel_order(
        order_id: str, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
    ) -> dict[str, Any] | JSONResponse:
        order = state.get(order_id)
        if order is None:
            return _error(404, "order_not_found", f"No existe el pedido {order_id}")
        if idempotency_key is not None and idempotency_key in cancels:
            return order
        if order["status"] not in NOT_SHIPPED:
            return _error(409, "already_shipped", "El pedido ya fue despachado")
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
        order = state.get(order_id)
        if order is None:
            return _error(404, "order_not_found", f"No existe el pedido {order_id}")
        if idempotency_key is not None and idempotency_key in refunds:
            return JSONResponse(status_code=201, content=refunds[idempotency_key])
        if order["status"] != "delivered":
            return _error(409, "not_delivered", "Solo se reembolsan pedidos entregados")
        if body.amount > order["total"]:
            return _error(409, "amount_too_high", "El monto supera el total del pedido")
        refund = {
            "id": f"R-{order_id}-{len(refunds) + 1}",
            "order_id": order_id,
            "amount": body.amount,
            "status": "requested",
            "created_at": _now(),
        }
        if idempotency_key is not None:
            refunds[idempotency_key] = refund
        return JSONResponse(status_code=201, content=refund)

    return app


app = create_app()
