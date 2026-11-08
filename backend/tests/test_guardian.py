"""Tarea 8 — test de aceptación: ninguna acción de riesgo alto pasa sin aprobación.

Un runtime espía registra cada llamada que llega a la API de Acme: así se prueba que la
acción no se ejecutó (no alcanza con mirar lo que devuelve el guardián).
"""

import uuid
from typing import Any

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.db.connection import tenant_scope
from app.policy.approvals import ApprovalError, decide
from app.policy.guardian import ContactContext, Guardian
from app.tools.runtime import ToolRuntime, UpstreamResult


class SpyRuntime(ToolRuntime):
    def __init__(self, inner: ToolRuntime) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    def call(
        self, method: str, path_template: str, params: dict[str, Any], *, key: str | None = None
    ) -> UpstreamResult:
        self.calls.append((method, path_template))
        return self._inner.call(method, path_template, params, key=key)

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


@pytest.fixture
def spy(stack: SimulatedStack) -> SpyRuntime:
    return SpyRuntime(ToolRuntime(stack.acme))


def _contact(stack: SimulatedStack, label: str) -> ContactContext:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select id, phone_hash, coalesce(verified_until > now(), false) from contacts"
            " where id = %s",
            (stack.seed.contacts[label],),
        ).fetchone()
    assert row is not None
    return ContactContext(id=row[0], phone_hash=row[1], verified=row[2])


def _case(stack: SimulatedStack, label: str) -> uuid.UUID:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (stack.seed.tenant_id, stack.seed.contacts[label]),
        ).fetchone()
    assert row is not None
    case_id: uuid.UUID = row[0]
    return case_id


ALL_TOOLS = {"consultar_pedido", "actualizar_direccion", "cancelar_pedido", "iniciar_reembolso"}


def _execute(stack: SimulatedStack, spy: SpyRuntime, label: str, tool: str, data: dict[str, Any]):  # type: ignore[no-untyped-def]
    guardian = Guardian(stack.conn, spy)
    return guardian.execute(
        tenant_id=stack.seed.tenant_id,
        case_id=_case(stack, label),
        contact=_contact(stack, label),
        tool_name=tool,
        tool_input=data,
        allowed=ALL_TOOLS,
    )


def test_ninguna_accion_de_riesgo_alto_pasa_sin_aprobacion(
    stack: SimulatedStack, spy: SpyRuntime
) -> None:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        high = [r[0] for r in stack.conn.execute("select name from tools where risk = 'high'")]
    assert high == ["iniciar_reembolso"]
    for amount in (1, 1000, 35000):
        decision = _execute(
            stack, spy, "martin", "iniciar_reembolso", {"order_id": "1005", "amount": amount}
        )
        assert decision.status == "approval_required"
        assert decision.approval_id is not None
    assert spy.writes == []


def test_riesgo_medio_se_ejecuta_bajo_el_limite_y_se_aprueba_sobre_el(
    stack: SimulatedStack, spy: SpyRuntime
) -> None:
    under = _execute(stack, spy, "lucia", "cancelar_pedido", {"order_id": "1002"})  # $28.500
    assert under.status == "executed"
    over = _execute(stack, spy, "lucia", "cancelar_pedido", {"order_id": "1004"})  # $152.000
    assert over.status == "approval_required"
    assert spy.writes == [("POST", "/orders/{order_id}/cancel")]  # solo la de $28.500


def test_monto_sobre_el_maximo_se_rechaza(stack: SimulatedStack, spy: SpyRuntime) -> None:
    decision = _execute(
        stack, spy, "martin", "iniciar_reembolso", {"order_id": "1005", "amount": 60000}
    )
    assert decision.status == "denied"
    assert spy.writes == []


def test_contacto_ajeno_o_no_verificado_no_ejecuta(stack: SimulatedStack, spy: SpyRuntime) -> None:
    other = _execute(stack, spy, "lucia", "iniciar_reembolso", {"order_id": "1005", "amount": 10})
    unverified = _execute(stack, spy, "sofia", "consultar_pedido", {"order_id": "1006"})
    assert (other.status, unverified.status) == ("denied", "denied")
    assert spy.writes == []


def test_aprobar_ejecuta_una_sola_vez(stack: SimulatedStack, spy: SpyRuntime) -> None:
    pending = _execute(
        stack, spy, "martin", "iniciar_reembolso", {"order_id": "1005", "amount": 35000}
    )
    assert pending.approval_id is not None
    guardian = Guardian(stack.conn, spy)
    outcome = decide(stack.conn, guardian, stack.seed.tenant_id, pending.approval_id, approve=True)
    assert outcome.status == "executed"
    assert spy.writes == [("POST", "/orders/{order_id}/refunds")]
    with pytest.raises(ApprovalError):
        decide(stack.conn, guardian, stack.seed.tenant_id, pending.approval_id, approve=True)
    assert len(spy.writes) == 1


def test_rechazar_no_ejecuta(stack: SimulatedStack, spy: SpyRuntime) -> None:
    pending = _execute(
        stack, spy, "martin", "iniciar_reembolso", {"order_id": "1005", "amount": 35000}
    )
    assert pending.approval_id is not None
    guardian = Guardian(stack.conn, spy)
    outcome = decide(stack.conn, guardian, stack.seed.tenant_id, pending.approval_id, approve=False)
    assert outcome.status == "rejected"
    assert spy.writes == []
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select status from approvals where id = %s", (pending.approval_id,)
        ).fetchone()
    assert row == ("rejected",)


def test_el_agente_pide_aprobacion_para_un_reembolso(stack: SimulatedStack) -> None:
    result = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert result.action == "pedir_aprobacion"
    assert result.approval_id is not None
    assert "revisión" in result.message
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select a.status, c.status from approvals a join cases c on c.id = a.case_id"
            " where a.id = %s",
            (result.approval_id,),
        ).fetchone()
    assert row == ("pending", "waiting_approval")
