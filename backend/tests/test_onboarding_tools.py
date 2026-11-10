"""Tarea 18 — test de aceptación: contra una API simulada, las herramientas generadas pasan sus
pruebas de contrato.

El modelo elige qué operaciones del OpenAPI del cliente necesita cada tipo de caso; el código
arma el manifiesto (esquema estricto, riesgo, verificación, control de dueño e idempotencia) a
partir de la operación. Ninguna herramienta queda aprobada: lo decide el cliente.
"""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from acme_api import OPENAPI_PATH, create_app
from app.onboarding.toolgen import build_manifests, contract_check, operations
from app.tools.manifest import MANIFEST_SCHEMA

SPEC: dict[str, Any] = json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8"))
# Valores de prueba para los campos que piden las operaciones de Acme.
EXAMPLES = {
    "order_id": "1002",
    "address": "Av. Corrientes 1234, CABA",
    "amount": 1000,
    "email": "lucia@example.com",
    "customer_phone": "+5491155550101",
}


class FakePicker:
    def pick_operations(
        self, case_types: list[tuple[str, str]], ops: list[tuple[str, str]]
    ) -> dict[str, list[str]]:
        every = [op for op, _ in ops]
        return {name: every for name, _ in case_types}


def test_lista_las_operaciones_del_openapi() -> None:
    found = {op.operation_id: (op.method, op.path) for op in operations(SPEC)}
    assert found["consultarPedido"] == ("GET", "/orders/{order_id}")
    assert found["iniciarReembolso"] == ("POST", "/orders/{order_id}/refunds")


def test_las_herramientas_generadas_cumplen_el_manifiesto_y_el_contrato() -> None:
    picked = FakePicker().pick_operations(
        [("donde_esta_mi_pedido", "Informar el estado del pedido")],
        [(op.operation_id, op.summary) for op in operations(SPEC)],
    )
    manifests = build_manifests(SPEC, picked)
    by_name = {m["name"]: m for m in manifests}
    validator = Draft202012Validator(MANIFEST_SCHEMA)
    for manifest in manifests:
        validator.validate(manifest)

    # Riesgo y controles salen de la operación: consultar es bajo; cancelar, medio; un
    # reembolso (dinero) es alto y siempre pide aprobación humana.
    assert by_name["consultar_pedido"]["risk"] == "low"
    assert by_name["cancelar_pedido"]["risk"] == "medium"
    refund = by_name["iniciar_reembolso"]
    assert refund["risk"] == "high" and refund["approval_over"] == 0
    assert refund["idempotency_key"] == "case_id + order_id"
    assert by_name["consultar_pedido"]["owner_check"] == "order.customer_phone == contact.phone"
    assert by_name["consultar_pedido"]["owner_lookup"] == {
        "method": "GET",
        "path": "/orders/{order_id}",
    }
    address = by_name["actualizar_direccion"]["input_schema"]
    assert address["required"] == ["order_id", "address"]
    assert address["additionalProperties"] is False
    # La búsqueda de clientes por email no es una herramienta del agente (es de verificación).
    assert "buscar_clientes" not in by_name

    client = TestClient(create_app())
    for manifest in manifests:
        result = contract_check(SPEC, manifest, client, EXAMPLES)
        assert result.ok, (manifest["name"], result.detail)
