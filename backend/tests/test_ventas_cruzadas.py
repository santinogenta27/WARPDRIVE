"""Hito 10, punto 3 — test de aceptación: ventas cruzadas con reglas (decisión 061).

- En el panel, la empresa elige qué producto ofrecer, en qué tipos de caso y con qué descuento
  máximo. No se puede elegir un tipo de caso que es un reclamo.
- El guardián aplica la regla en código: hasta el descuento máximo, una oferta por conversación,
  nunca con el cliente enojado ni en medio de un reclamo, y solo con stock.
- El descuento de la oferta pasa al link de compra solo para el producto ofrecido y hasta su
  porcentaje.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import ContactContext
from app.sales import upsell

ACME_EMAIL, ACME_PASSWORD = "operador@plataforma.example", "clave-del-operador"


@pytest.fixture
def northwind(conn: psycopg.Connection) -> Any:
    return build_simulated_stack(conn, company="northwind")


def _case(stack: Any, kind: str, who: str = "sophia") -> uuid.UUID:
    tenant = stack.seed.tenant_id
    with tenant_scope(stack.conn, tenant):
        row = stack.conn.execute(
            "insert into cases (tenant_id, contact_id, case_type_id)"
            " select %s, %s, id from case_types where name = %s returning id",
            (tenant, stack.seed.contacts[who], kind),
        ).fetchone()
    assert row is not None
    case_id: uuid.UUID = row[0]
    return case_id


def _offer(stack: Any, case_id: uuid.UUID, discount: float, message: str = "Thanks!") -> Any:
    return stack.agent._guardian.offer_product(
        tenant_id=stack.seed.tenant_id,
        case_id=case_id,
        message=message,
        tool_input={"product": "Wool Beanie", "discount_percent": discount},
    )


# -- 1. la regla ------------------------------------------------------------------------------
def test_northwind_ofrece_el_gorro_en_preventa_hasta_15(northwind: Any) -> None:
    rules = upsell.load(northwind.conn, northwind.seed.tenant_id)
    assert [(r.product, r.case_types, r.max_discount) for r in rules] == [
        ("Wool Beanie", ["shopping"], 15.0)
    ]
    complaints = upsell.complaint_types(northwind.conn, northwind.seed.tenant_id)
    assert {"refund", "cancel_order", "exchange"} <= complaints
    assert "shopping" not in complaints
    with pytest.raises(upsell.UpsellError):
        upsell.add(northwind.conn, northwind.seed.tenant_id, "Wool Beanie", ["refund"], Decimal(5))
    with pytest.raises(upsell.UpsellError):
        upsell.parse_discount("80")


# -- 2. el guardián -----------------------------------------------------------------------------
def test_la_oferta_dentro_del_limite_trae_el_producto_real(northwind: Any) -> None:
    case_id = _case(northwind, "shopping")
    decision = _offer(northwind, case_id, 15)
    assert decision.status == "executed", decision
    product = decision.result["data"]["product"]
    assert (product["name"], product["price"]) == ("Wool Beanie", 24.0)
    assert decision.result["data"]["discount_percent"] == 15


def test_un_descuento_sobre_el_maximo_se_rechaza_y_no_cuenta_como_oferta(northwind: Any) -> None:
    case_id = _case(northwind, "shopping")
    denied = _offer(northwind, case_id, 30)
    assert denied.status == "denied" and denied.result["maximo_permitido"] == 15
    assert not upsell.offered(northwind.conn, northwind.seed.tenant_id, case_id)


def test_una_sola_oferta_por_conversacion(northwind: Any) -> None:
    case_id = _case(northwind, "shopping")
    assert _offer(northwind, case_id, 0).status == "executed"
    again = _offer(northwind, case_id, 0)
    assert again.status == "denied" and "Ya hubo una oferta" in again.reason


def test_nunca_con_el_cliente_enojado(northwind: Any) -> None:
    case_id = _case(northwind, "shopping")
    angry = _offer(northwind, case_id, 0, "This is ridiculous, the site is broken!!")
    assert angry.status == "denied" and "molesto" in angry.reason
    enojado = _offer(northwind, case_id, 0, "Es una vergüenza, estoy harta")
    assert enojado.status == "denied"


def test_nunca_en_medio_de_un_reclamo(northwind: Any) -> None:
    tenant = northwind.seed.tenant_id
    # Aunque una regla lo pidiera (cargada a mano, el panel no lo deja), un reclamo no vende.
    with tenant_scope(northwind.conn, tenant):
        northwind.conn.execute("update upsell_rules set case_types = array['shopping', 'refund']")
    refund = _case(northwind, "refund")
    assert _offer(northwind, refund, 0).status == "denied"
    shopping = _case(northwind, "shopping")
    with tenant_scope(northwind.conn, tenant):
        northwind.conn.execute(
            "update cases set status = 'waiting_approval' where id = %s", (shopping,)
        )
    assert _offer(northwind, shopping, 0).status == "denied"


def test_el_descuento_pasa_al_link_solo_si_salio_de_la_oferta(northwind: Any) -> None:
    case_id = _case(northwind, "shopping")
    guardian = northwind.agent._guardian
    contact = ContactContext(northwind.seed.contacts["sophia"], hash_phone("+1"), False)

    def checkout(items: list[dict[str, Any]], discount: float) -> Any:
        return guardian.execute(
            tenant_id=northwind.seed.tenant_id,
            case_id=case_id,
            contact=contact,
            tool_name="armar_carrito",
            tool_input={"items": items, "discount_percent": discount},
            allowed={"armar_carrito"},
        )

    beanie = [{"sku": "NW-WBN-NVY-OS", "quantity": 1}]
    assert checkout(beanie, 15).status == "denied"  # sin oferta, sin descuento
    assert _offer(northwind, case_id, 15).status == "executed"
    jacket = [{"sku": "NW-TRJ-BLK-M", "quantity": 1}]
    assert checkout(jacket, 15).status == "denied"  # el descuento es solo del gorro
    assert checkout(beanie, 20).status == "denied"  # más que la oferta
    made = checkout(beanie, 15)
    assert made.status == "executed", made
    assert made.result["data"]["total"] == 20.4
    assert made.result["data"]["url"].endswith("?discount=NW15OFF")


# -- 3. el agente -----------------------------------------------------------------------------
def test_despues_del_link_ofrece_el_gorro_una_sola_vez(northwind: Any) -> None:
    northwind.send("sophia", "Do you have a rain jacket in size M?")
    result = northwind.send("sophia", "Great, I'll take the black one")
    executed = [t.name for t in result.tool_calls if t.status == "executed"]
    assert executed == ["buscar_productos", "armar_carrito", "ofrecer_producto"]
    assert "https://shop.northwind.example/cart/NW-TRJ-BLK-M:1" in result.message
    assert "Wool Beanie is $24" in result.message and "15% off" in result.message
    tenant = northwind.seed.tenant_id
    assert upsell.offerable(northwind.conn, tenant, result.case_id, "shopping", "ok") == []


def test_un_descuento_alto_lo_frena_el_guardian(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="descuento_alto", company="northwind")
    stack.send("sophia", "Do you have a rain jacket in size M?")
    result = stack.send("sophia", "Great, I'll take the black one")
    offers = [t for t in result.tool_calls if t.name == "ofrecer_producto"]
    assert [(t.input["discount_percent"], t.status) for t in offers] == [
        (30.0, "denied"),
        (15.0, "executed"),
    ]


def test_con_el_cliente_enojado_no_se_ofrece_nada(northwind: Any) -> None:
    northwind.send("sophia", "This is ridiculous, I've been waiting for weeks for a rain jacket!!")
    result = northwind.send("sophia", "I'll take the black rain jacket in size M")
    assert "ofrecer_producto" not in [t.name for t in result.tool_calls]
    assert "Wool Beanie" not in result.message


# -- 4. el panel ------------------------------------------------------------------------------
@pytest.fixture
def acme(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setenv("PANEL_EMAIL", ACME_EMAIL)
    monkeypatch.setenv("PANEL_PASSWORD", ACME_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield stack.seed.tenant_id
    finally:
        app.dependency_overrides.clear()


def test_el_panel_agrega_y_quita_productos_para_ofrecer(
    conn: psycopg.Connection, acme: uuid.UUID
) -> None:
    client = TestClient(app)
    client.post("/panel/login", data={"email": ACME_EMAIL, "password": ACME_PASSWORD})
    page = client.get("/panel/configuracion").text
    assert "Ventas cruzadas" in page
    assert 'name="tipo_reembolso"' not in page  # un reclamo no se puede elegir
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    kind = upsell.eligible_types(conn, acme)[0]
    saved = client.post(
        "/panel/configuracion/ventas",
        data={
            "csrf": token.group(1),
            "producto": "Gorro de lana",
            f"tipo_{kind}": "si",
            "descuento": "10",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    rules = upsell.load(conn, acme)
    assert [(r.product, r.case_types, r.max_discount) for r in rules] == [
        ("Gorro de lana", [kind], 10.0)
    ]
    refused = client.post(
        "/panel/configuracion/ventas",
        data={"csrf": token.group(1), "producto": "Gorro", "tipo_reembolso": "si"},
        follow_redirects=False,
    )
    assert refused.status_code == 303 and len(upsell.load(conn, acme)) == 1
    client.post(
        f"/panel/configuracion/ventas/{rules[0].id}/borrar",
        data={"csrf": token.group(1)},
        follow_redirects=False,
    )
    assert upsell.load(conn, acme) == []
