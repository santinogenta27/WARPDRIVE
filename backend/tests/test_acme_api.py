"""Tarea 5 — test de aceptación: la API simulada de Acme responde según su OpenAPI.

Cada operación y cada código de respuesta documentado se ejercita, y el cuerpo se valida contra
el esquema del contrato. También se verifica lo inverso: que no haya respuestas sin documentar.
"""

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from acme_api import OPENAPI_PATH, create_app

SPEC: dict[str, Any] = json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8"))

# (método, ruta del contrato, ruta concreta, cuerpo, headers, código esperado)
EXAMPLES: list[tuple[str, str, str, dict[str, Any] | None, dict[str, str], int]] = [
    ("get", "/orders", "/orders?customer_phone=%2B5491155550101", None, {}, 200),
    ("get", "/customers", "/customers?email=lucia%40example.com", None, {}, 200),
    ("get", "/orders/{order_id}", "/orders/1001", None, {}, 200),
    ("get", "/orders/{order_id}", "/orders/9999", None, {}, 404),
    (
        "patch",
        "/orders/{order_id}/address",
        "/orders/1002/address",
        {"address": "Av. Corrientes 1234, CABA"},
        {},
        200,
    ),
    (
        "patch",
        "/orders/{order_id}/address",
        "/orders/9999/address",
        {"address": "Av. Corrientes 1234, CABA"},
        {},
        404,
    ),
    (
        "patch",
        "/orders/{order_id}/address",
        "/orders/1001/address",
        {"address": "Av. Corrientes 1234, CABA"},
        {},
        409,
    ),
    (
        "post",
        "/orders/{order_id}/cancel",
        "/orders/1004/cancel",
        None,
        {"Idempotency-Key": "c1"},
        200,
    ),
    ("post", "/orders/{order_id}/cancel", "/orders/9999/cancel", None, {}, 404),
    ("post", "/orders/{order_id}/cancel", "/orders/1001/cancel", None, {}, 409),
    (
        "post",
        "/orders/{order_id}/refunds",
        "/orders/1005/refunds",
        {"amount": 35000},
        {"Idempotency-Key": "r1"},
        201,
    ),
    ("post", "/orders/{order_id}/refunds", "/orders/9999/refunds", {"amount": 10}, {}, 404),
    ("post", "/orders/{order_id}/refunds", "/orders/1002/refunds", {"amount": 10}, {}, 409),
]


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target: Any = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _response_schema(path: str, method: str, status: int) -> dict[str, Any]:
    response = _resolve(SPEC["paths"][path][method]["responses"][str(status)])
    schema = response["content"]["application/json"]["schema"]
    return {**schema, "components": SPEC["components"]}


@pytest.mark.parametrize(("method", "path", "url", "body", "headers", "status"), EXAMPLES)
def test_responde_segun_el_contrato(
    method: str,
    path: str,
    url: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
    status: int,
) -> None:
    client = TestClient(create_app())
    response = client.request(method.upper(), url, json=body, headers=headers)
    assert response.status_code == status, response.text
    Draft202012Validator(_response_schema(path, method, status)).validate(response.json())


def test_todas_las_respuestas_documentadas_estan_probadas() -> None:
    documented = {
        (method, path, int(code))
        for path, operations in SPEC["paths"].items()
        for method, operation in operations.items()
        for code in operation["responses"]
    }
    tested = {(method, path, status) for method, path, _, _, _, status in EXAMPLES}
    assert documented == tested


def test_reembolso_con_la_misma_clave_no_se_duplica() -> None:
    client = TestClient(create_app())
    headers = {"Idempotency-Key": "case-1:1005"}
    first = client.post("/orders/1005/refunds", json={"amount": 1000}, headers=headers).json()
    second = client.post("/orders/1005/refunds", json={"amount": 1000}, headers=headers).json()
    assert first == second


def test_herramientas_de_acme_salen_del_flujo_y_cumplen_el_manifiesto(
    conn: psycopg.Connection,
) -> None:
    """Hito 6: las herramientas de Acme se generan desde su OpenAPI y se aprueban con sus
    límites en la revisión, como las de cualquier empresa (decisión 026)."""
    from simulated.stack import build_simulated_stack

    from app.db.connection import tenant_scope
    from app.tools.manifest import MANIFEST_SCHEMA

    stack = build_simulated_stack(conn)
    with tenant_scope(conn, stack.seed.tenant_id):
        rows = conn.execute("select name, manifest, approved from tools").fetchall()
        lookup = conn.execute(
            "select settings -> 'identity_lookup' from tenants where id = %s",
            (stack.seed.tenant_id,),
        ).fetchone()
    manifests = {name: manifest for name, manifest, _ in rows}
    assert set(manifests) == {
        "consultar_pedido",
        "actualizar_direccion",
        "cancelar_pedido",
        "iniciar_reembolso",
    }
    assert all(approved for _, _, approved in rows)
    for name, manifest in manifests.items():
        Draft202012Validator(MANIFEST_SCHEMA).validate(manifest)
        assert manifest["name"] == name
        upstream = manifest["upstream"]
        assert upstream["method"].lower() in SPEC["paths"][upstream["path"]], name
        assert manifest["requires_verified_contact"] and manifest["owner_check"], name
    risks = {name: m["risk"] for name, m in manifests.items()}
    assert risks == {
        "consultar_pedido": "low",
        "actualizar_direccion": "low",
        "cancelar_pedido": "medium",
        "iniciar_reembolso": "high",
    }
    # Los límites que fijó la empresa al revisar.
    refund, cancel = manifests["iniciar_reembolso"], manifests["cancelar_pedido"]
    assert (refund["max_amount"], refund["approval_over"]) == (50000, 0)
    assert (cancel["approval_over"], cancel["amount_from"]) == (100000, "order.total")
    # La búsqueda de clientes por email (verificación por código) sale del OpenAPI.
    assert lookup is not None and lookup[0]["path"] == "/customers"


def test_busca_clientes_por_email_sin_distinguir_mayusculas() -> None:
    client = TestClient(create_app())
    found = client.get("/customers", params={"email": " Lucia@Example.com "}).json()["customers"]
    assert [c["phone"] for c in found] == ["+5491155550101"]
    assert client.get("/customers", params={"email": "nadie@example.com"}).json() == {
        "customers": []
    }


def test_cada_app_arranca_con_datos_limpios() -> None:
    TestClient(create_app()).patch("/orders/1002/address", json={"address": "Otra dirección 1"})
    fresh = TestClient(create_app()).get("/orders/1002").json()
    assert fresh["shipping_address"] == "Calle Los Aromos 123, Rosario"
