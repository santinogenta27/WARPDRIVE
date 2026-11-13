"""Hito 8, tarea 4 — test de aceptación de las herramientas nuevas de Northwind (decisión 039).

La API simulada suma tres operaciones, con su OpenAPI, y cada una llega al agente como una
herramienta generada desde el contrato que pasa por el guardián:
- `iniciar_cambio`: cambio de talle, color o producto de un pedido entregado;
- `ofrecer_cupon`: cupón de cortesía, con tope del 10 % según la política;
- `ofrecer_envio_express`: envío express gratis en la próxima compra.

Reglas, aplicadas en código: un reembolso de más de 100 USD pide aprobación; un descuento fuera
de la política se rechaza sin llegar a la API, y el rechazo trae la alternativa permitida.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.demo import northwind
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.policy.guardian import ContactContext, GuardDecision, Guardian
from app.tools.manifest import MANIFEST_SCHEMA
from app.tools.runtime import ToolRuntime

NEW_TOOLS = {"iniciar_cambio", "ofrecer_cupon", "ofrecer_envio_express"}
PHONES = {"emily": "+15555550101", "daniel": "+15555550102", "sophia": "+15555550103"}


@pytest.fixture
def embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    import httpx
    from simulated.voyage import voyage_transport

    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


class Store:
    """Northwind con su API simulada propia (limpia en cada test) y el guardián."""

    def __init__(self, conn: psycopg.Connection, embedder: VoyageEmbedder) -> None:
        self.conn = conn
        self.seed = northwind.seed_northwind(conn, embedder)
        self.api: TestClient = northwind.sandbox_client()
        self.guardian = Guardian(conn, ToolRuntime(self.api))
        with tenant_scope(conn, self.seed.tenant_id):
            row = conn.execute(
                "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
                (self.seed.tenant_id, self.seed.contacts["emily"]),
            ).fetchone()
        assert row is not None
        self.case_id: uuid.UUID = row[0]

    def manifests(self) -> dict[str, dict[str, Any]]:
        with tenant_scope(self.conn, self.seed.tenant_id):
            rows = self.conn.execute("select name, manifest, approved from tools").fetchall()
        return {name: manifest for name, manifest, approved in rows if approved}

    def run(
        self,
        tool: str,
        params: dict[str, Any],
        *,
        who: str = "emily",
        verified: bool = True,
        case_id: uuid.UUID | None = None,
    ) -> GuardDecision:
        contact = ContactContext(self.seed.contacts[who], hash_phone(PHONES[who]), verified)
        return self.guardian.execute(
            tenant_id=self.seed.tenant_id,
            case_id=case_id or self.case_id,
            contact=contact,
            tool_name=tool,
            tool_input=params,
            allowed=NEW_TOOLS | {"iniciar_reembolso", "consultar_pedido"},
        )


@pytest.fixture
def store(conn: psycopg.Connection, embedder: VoyageEmbedder) -> Store:
    return Store(conn, embedder)


# -- 1. las herramientas salen del OpenAPI y quedan aprobadas con sus límites ------------------
def test_las_herramientas_nuevas_salen_del_openapi_con_sus_controles(store: Store) -> None:
    manifests = store.manifests()
    assert NEW_TOOLS <= set(manifests)
    for name in NEW_TOOLS:
        manifest = manifests[name]
        Draft202012Validator(MANIFEST_SCHEMA).validate(manifest)
        # Actúan sobre un pedido del cliente: identidad verificada y control de dueño.
        assert manifest["requires_verified_contact"] is True, name
        assert manifest["owner_check"] == "order.customer_phone == contact.phone", name
        # Una vez por caso y pedido: repetirla no duplica el efecto.
        assert manifest["idempotency_key"] == "case_id + order_id", name
        assert manifest["upstream"]["method"] == "POST", name
    coupon = manifests["ofrecer_cupon"]
    assert (coupon["amount_from"], coupon["max_amount"]) == ("input.percent", 10)
    assert coupon["alternatives"] == ["ofrecer_envio_express"]
    refund = manifests["iniciar_reembolso"]
    assert (refund["risk"], refund["approval_over"]) == ("medium", 100)


def test_cada_tipo_de_caso_ofrece_sus_herramientas(store: Store) -> None:
    from app.companies import review

    tools = {
        kind.name: set(review.parse_instructions(kind.instructions).get("herramientas") or [])
        for kind in review.case_types(store.conn, store.seed.tenant_id)
    }
    assert "iniciar_cambio" in tools["exchange"]
    assert {"ofrecer_cupon", "ofrecer_envio_express"} <= tools["where_is_my_order"]


# -- 2. cambio de talle o de producto ---------------------------------------------------------
def test_el_cambio_de_talle_se_ejecuta(store: Store) -> None:
    decision = store.run(
        "iniciar_cambio",
        {"order_id": "NW-4533", "item_sku": "NW-MCS-NVY-M", "new_size": "L"},
    )
    assert decision.status == "executed", decision.result
    exchange = decision.result["data"]
    assert exchange["from_item"]["size"] == "M" and exchange["to_item"]["size"] == "L"
    assert exchange["price_difference"] == 0 and exchange["return_label"] == "emailed"


def test_el_cambio_por_otro_producto_calcula_la_diferencia(store: Store) -> None:
    decision = store.run(
        "iniciar_cambio",
        {
            "order_id": "NW-4533",
            "item_sku": "NW-MCS-NVY-M",
            "new_product": "Alpine Down Vest",
            "new_size": "M",
            "new_color": "Black",  # el chaleco no viene en Navy: la API lo rechazaría
        },
    )
    assert decision.status == "executed", decision.result
    assert decision.result["data"]["price_difference"] == 40  # $129 - $89: paga la diferencia


@pytest.mark.parametrize(
    ("params", "why"),
    [
        ({"order_id": "NW-4521", "item_sku": "NW-TRJ-BLU-M", "new_size": "L"}, "not_delivered"),
        ({"order_id": "NW-4533", "item_sku": "NW-XXX", "new_size": "L"}, "item_not_in_order"),
        ({"order_id": "NW-4533", "item_sku": "NW-MCS-NVY-M", "new_size": "XXL"}, "size"),
    ],
)
def test_un_cambio_que_no_corresponde_no_se_hace(
    store: Store, params: dict[str, Any], why: str
) -> None:
    decision = store.run("iniciar_cambio", params)
    assert decision.status == "failed" and "error" in decision.result, why


def test_cada_prenda_se_cambia_una_sola_vez(store: Store) -> None:
    params = {"order_id": "NW-4533", "item_sku": "NW-MCS-NVY-M", "new_size": "L"}
    first = store.run("iniciar_cambio", params)
    again = store.run("iniciar_cambio", params)  # mismo caso: la misma respuesta, sin duplicar
    assert first.status == again.status == "executed"
    assert first.result["data"]["id"] == again.result["data"]["id"]
    with tenant_scope(store.conn, store.seed.tenant_id):
        other = store.conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (store.seed.tenant_id, store.seed.contacts["emily"]),
        ).fetchone()
    assert other is not None
    later = store.run("iniciar_cambio", {**params, "new_size": "S"}, case_id=other[0])
    assert later.status == "failed"  # la prenda ya se cambió


# -- 3. cupón de cortesía, con el tope de la política -----------------------------------------
def test_el_cupon_dentro_de_la_politica_se_ejecuta(store: Store) -> None:
    decision = store.run("ofrecer_cupon", {"order_id": "NW-4521", "percent": 10})
    assert decision.status == "executed", decision.result
    coupon = decision.result["data"]
    assert coupon["percent"] == 10 and coupon["code"] and coupon["valid_days"] == 60


def test_un_descuento_fuera_de_la_politica_se_rechaza_con_la_alternativa(store: Store) -> None:
    decision = store.run("ofrecer_cupon", {"order_id": "NW-4521", "percent": 25})
    assert decision.status == "denied"
    assert decision.result["maximo_permitido"] == 10
    assert {"herramienta": "ofrecer_envio_express"} in decision.result["alternativas"]
    assert {"herramienta": "ofrecer_cupon", "maximo": 10} in decision.result["alternativas"]
    # No llegó a la API: el cupón permitido todavía se puede dar.
    assert store.api.post("/orders/NW-4521/coupons", json={"percent": 10}).status_code == 201


def test_el_cupon_y_el_envio_piden_identidad_y_un_pedido_propio(store: Store) -> None:
    assert (
        store.run("ofrecer_cupon", {"order_id": "NW-4521", "percent": 10}, verified=False).status
        == "denied"
    )
    other = store.run("ofrecer_cupon", {"order_id": "NW-4540", "percent": 10})
    assert other.status == "denied" and "Daniel" not in str(other.result)
    express = store.run("ofrecer_envio_express", {"order_id": "NW-4540"})
    assert express.status == "denied"


# -- 4. envío express gratis, y una sola compensación por pedido ------------------------------
def test_el_envio_express_se_ejecuta_y_no_se_suma_a_un_cupon(store: Store) -> None:
    express = store.run("ofrecer_envio_express", {"order_id": "NW-4521"})
    assert express.status == "executed", express.result
    offer = express.result["data"]
    assert offer["applies_to"] == "next_order" and offer["code"]
    # Una cortesía por pedido: después del envío gratis, no hay cupón para el mismo pedido.
    coupon = store.run("ofrecer_cupon", {"order_id": "NW-4521", "percent": 10})
    assert coupon.status == "failed"


# -- 5. los reembolsos siguen con su regla ----------------------------------------------------
def test_el_reembolso_de_mas_de_100_usd_pide_aprobacion(store: Store) -> None:
    high = store.run("iniciar_reembolso", {"order_id": "NW-4528", "amount": 213})
    assert high.status == "approval_required"
    # Un monto parcial de hasta 100 USD del mismo pedido no pide aprobación.
    partial = store.run("iniciar_reembolso", {"order_id": "NW-4528", "amount": 24})
    assert partial.status == "executed", partial.result


def test_el_reembolso_de_hasta_100_usd_se_hace_solo(store: Store) -> None:
    low = store.run("iniciar_reembolso", {"order_id": "NW-4550", "amount": 56}, who="sophia")
    assert low.status == "executed", low.result
