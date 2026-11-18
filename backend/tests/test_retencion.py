"""Hito 10, punto 4 — test de aceptación: retención (decisión 062).

Ante una cancelación o una devolución, primero se ofrecen las alternativas que la política
permite (cambio, crédito, cupón). Si el cliente insiste, se sigue con su pedido sin volver a
ofrecer. Lo aplica el guardián en código: la primera llamada a la acción no se hace y trae las
alternativas aprobadas; la siguiente, en otro mensaje del cliente, sigue.
"""

import uuid
from typing import Any

import psycopg
import pytest
from simulated.stack import build_simulated_stack

from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.policy.guardian import RETENTION_REASON, ContactContext

PHONES = {"emily": "+15555550101", "daniel": "+15555550102", "sophia": "+15555550103"}


@pytest.fixture
def northwind(conn: psycopg.Connection) -> Any:
    return build_simulated_stack(conn, company="northwind")


def _case(stack: Any, who: str) -> uuid.UUID:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (stack.seed.tenant_id, stack.seed.contacts[who]),
        ).fetchone()
    assert row is not None
    case_id: uuid.UUID = row[0]
    return case_id


def _run(stack: Any, case_id: uuid.UUID, tool: str, params: dict[str, Any], who: str) -> Any:
    contact = ContactContext(stack.seed.contacts[who], hash_phone(PHONES[who]), True)
    return stack.agent._guardian.execute(
        tenant_id=stack.seed.tenant_id,
        case_id=case_id,
        contact=contact,
        tool_name=tool,
        tool_input=params,
        allowed={tool, "consultar_pedido"},
    )


# -- 1. el guardián -----------------------------------------------------------------------------
def test_el_primer_reembolso_trae_las_alternativas_y_el_segundo_sigue(northwind: Any) -> None:
    case_id = _case(northwind, "emily")
    refund = {"order_id": "NW-4533", "amount": 89}
    first = _run(northwind, case_id, "iniciar_reembolso", refund, "emily")
    assert first.status == "denied" and first.reason == RETENTION_REASON
    assert first.result["retencion"] is True
    assert [a["herramienta"] for a in first.result["alternativas"]] == [
        "iniciar_cambio",
        "ofrecer_cupon",
    ]
    # El cliente insiste: el reembolso sigue (hasta 100 USD, sin aprobación) y no se vuelve a
    # ofrecer nada.
    second = _run(northwind, case_id, "iniciar_reembolso", refund, "emily")
    assert second.status == "executed", second
    third = _run(northwind, case_id, "iniciar_reembolso", refund, "emily")
    assert third.reason != RETENTION_REASON


def test_la_cancelacion_ofrece_el_cupon_antes(northwind: Any) -> None:
    case_id = _case(northwind, "daniel")
    first = _run(northwind, case_id, "cancelar_pedido", {"order_id": "NW-4545"}, "daniel")
    assert [a["herramienta"] for a in first.result["alternativas"]] == ["ofrecer_cupon"]
    second = _run(northwind, case_id, "cancelar_pedido", {"order_id": "NW-4545"}, "daniel")
    assert second.status == "executed" and second.result["data"]["status"] == "cancelled"


def test_un_pedido_ajeno_no_revela_nada_ni_ofrece_alternativas(northwind: Any) -> None:
    case_id = _case(northwind, "sophia")
    found = _run(
        northwind, case_id, "iniciar_reembolso", {"order_id": "NW-4533", "amount": 89}, "sophia"
    )
    assert found.status == "denied" and "retencion" not in found.result


def test_solo_cuentan_las_alternativas_aprobadas(northwind: Any) -> None:
    from app.companies import review

    tenant = northwind.seed.tenant_id
    for tool in review.tools(northwind.conn, tenant):
        if tool.name in ("iniciar_cambio", "ofrecer_cupon"):
            review.set_tool(northwind.conn, tenant, tool.id, approved=False)
    case_id = _case(northwind, "emily")
    refund = {"order_id": "NW-4533", "amount": 89}
    assert _run(northwind, case_id, "iniciar_reembolso", refund, "emily").status == "executed"


# -- 2. el agente -----------------------------------------------------------------------------
def test_el_agente_ofrece_primero_y_si_el_cliente_insiste_reembolsa(northwind: Any) -> None:
    first = northwind.send("emily", "I want a refund for order NW-4528")
    assert first.action == "responder"
    assert "exchange" in first.message and "coupon" in first.message
    assert not any(
        t.status in ("executed", "approval_required") and t.name == "iniciar_reembolso"
        for t in first.tool_calls
    )
    second = northwind.send("emily", "No thanks, I just want my money back")
    assert second.case_id == first.case_id
    assert second.action == "pedir_aprobacion"  # 213 USD: sigue con su pedido y pide aprobación
    assert "coupon" not in second.message and "exchange" not in second.message


def test_si_el_cliente_acepta_el_cambio_no_hay_reembolso(northwind: Any) -> None:
    northwind.send("emily", "I want a refund for order NW-4528")
    result = northwind.send(
        "emily", "Actually, I'd rather exchange the boots from order NW-4528 for a size 9."
    )
    executed = [t.name for t in result.tool_calls if t.status == "executed"]
    assert "iniciar_cambio" in executed and "iniciar_reembolso" not in executed


def test_si_el_modelo_no_espera_al_cliente_el_ciclo_lo_frena(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="insiste_solo", company="northwind")
    result = stack.send("emily", "I want a refund for order NW-4528")
    refunds = [t for t in result.tool_calls if t.name == "iniciar_reembolso"]
    assert [t.status for t in refunds] == ["denied", "denied"]
    assert result.action == "responder" and result.approval_id is None
    assert "coupon" in result.message
