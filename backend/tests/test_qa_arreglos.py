"""Hito QA — tests de los arreglos que salieron de los evals con Groq real (decisión 066)."""

import uuid
from typing import Any

import psycopg
import pytest
from simulated.stack import build_simulated_stack

from app.agent import conduct
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.policy.guardian import RETENTION_REASON, ContactContext


# -- 1. apóstrofos y guiones tipográficos -------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "I’ve started the refund review for your order NW‑4528.",
        "I’ve charged your card.",
    ],
)
def test_los_controles_reconocen_el_apostrofo_tipografico(text: str) -> None:
    assert conduct.claims_action(text, "en") or conduct.claims_payment(text)


def test_el_enojo_con_apostrofo_tipografico() -> None:
    assert conduct.upset_any_language("It still hasn’t arrived!!")


# -- 2. no pedir el número de pedido que el cliente ya dio --------------------------------------
@pytest.mark.parametrize(
    ("reply", "message", "order"),
    [
        (
            "I need the order number to check it. Could you confirm the order ID?",
            "Where is my order NW-4550?",
            "NW-4550",
        ),
        ("¿Me pasás el número de pedido?", "¿Dónde está el pedido 1005?", "1005"),
        ("What's your order number?", "Change the address to 500 Pine St, WA 98101", None),
        ("Your order NW-4550 was delivered.", "Where is my order NW-4550?", None),
    ],
)
def test_pedir_un_pedido_ya_dado(reply: str, message: str, order: str | None) -> None:
    assert conduct.asks_given_order(reply, message) == order


def test_el_agente_no_vuelve_a_pedir_el_pedido(conn: psycopg.Connection) -> None:
    from app.audit.log import reconstruct_case

    stack = build_simulated_stack(conn, mode="pide_el_pedido", company="northwind")
    result = stack.send("sophia", "Where is my order NW-4550?")
    events = reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id)
    assert any(e.kind == "grounding_rejected" for e in events)
    assert result.verification_requested  # usó el pedido: la herramienta pidió la verificación


# -- 3. retención: el turno siguiente sabe qué ya se ofreció ------------------------------------
@pytest.fixture
def northwind(conn: psycopg.Connection) -> Any:
    return build_simulated_stack(conn, company="northwind")


def test_el_turno_siguiente_sabe_que_las_alternativas_ya_se_ofrecieron(northwind: Any) -> None:
    northwind.send("emily", "I want a refund for order NW-4528")
    northwind.send("emily", "No thanks, I just want the refund.")
    system = next(
        str(r["body"].get("system"))
        for r in reversed(northwind.claude_requests)
        if "already_offered" in str(r["body"].get("system"))
    )
    assert "iniciar_reembolso on order NW-4528 were already offered" in system


def test_insistir_con_otra_forma_del_numero_no_vuelve_a_ofrecer(northwind: Any) -> None:
    tenant = northwind.seed.tenant_id
    with tenant_scope(northwind.conn, tenant):
        row = northwind.conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (tenant, northwind.seed.contacts["emily"]),
        ).fetchone()
    assert row is not None
    case_id: uuid.UUID = row[0]
    contact = ContactContext(northwind.seed.contacts["emily"], hash_phone("+15555550101"), True)

    def refund(order: str) -> Any:
        return northwind.agent._guardian.execute(
            tenant_id=tenant,
            case_id=case_id,
            contact=contact,
            tool_name="iniciar_reembolso",
            tool_input={"order_id": order, "amount": 89},
            allowed={"iniciar_reembolso"},
        )

    assert refund("NW-4533").reason == RETENTION_REASON
    assert refund("4533").status == "executed"
