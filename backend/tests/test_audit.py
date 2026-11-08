"""Tarea 10 — test de aceptación: un caso se reconstruye completo; alterar un evento rompe la
cadena."""

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.audit.log import reconstruct_case, verify_chain


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def test_un_caso_se_reconstruye_completo(stack: SimulatedStack) -> None:
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    events = reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id)

    assert [e.kind for e in events] == [
        "message_received",
        "model_call",  # clasificador
        "classified",
        "rag_search",
        "model_call",  # agente: pide consultar_pedido
        "tool_call",
        "model_call",  # agente: responde
        "final_action",
    ]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    by_kind = {e.kind: e.payload for e in events}
    assert by_kind["message_received"]["text"] == "¿Dónde está mi pedido 1001?"
    assert by_kind["classified"]["case_type"] == "donde_esta_mi_pedido"
    assert by_kind["tool_call"]["tool"] == "consultar_pedido"
    assert by_kind["tool_call"]["status"] == "executed"
    assert by_kind["final_action"]["message"] == result.message
    assert by_kind["rag_search"]["sources"]
    model_calls = [e.payload for e in events if e.kind == "model_call"]
    assert [m["model"] for m in model_calls] == [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-sonnet-5-5",
    ]
    assert all(float(m["cost_usd"]) > 0 for m in model_calls)
    assert verify_chain(stack.conn, stack.seed.tenant_id, result.case_id).ok


def _tamper(stack: SimulatedStack, sql: str, params: tuple) -> None:  # type: ignore[type-arg]
    """Simula a alguien con acceso de dueño a la base que salta el trigger de solo inserción."""
    stack.conn.execute("alter table audit_events disable trigger audit_events_no_update_delete")
    stack.conn.execute(sql, params)
    stack.conn.execute("alter table audit_events enable trigger audit_events_no_update_delete")


def test_alterar_un_evento_rompe_la_cadena(stack: SimulatedStack) -> None:
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    _tamper(
        stack,
        "update audit_events set payload = jsonb_set(payload, '{text}', '\"otro texto\"')"
        " where case_id = %s and seq = 1",
        (result.case_id,),
    )
    verdict = verify_chain(stack.conn, stack.seed.tenant_id, result.case_id)
    assert not verdict.ok
    assert verdict.broken_at == 1


def test_borrar_un_evento_rompe_la_cadena(stack: SimulatedStack) -> None:
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    _tamper(stack, "delete from audit_events where case_id = %s and seq = 3", (result.case_id,))
    verdict = verify_chain(stack.conn, stack.seed.tenant_id, result.case_id)
    assert not verdict.ok
    assert verdict.broken_at == 4


def test_una_derivacion_y_una_aprobacion_quedan_auditadas(stack: SimulatedStack) -> None:
    escalated = stack.send("lucia", "Quiero hablar con una persona")
    kinds = [e.kind for e in reconstruct_case(stack.conn, stack.seed.tenant_id, escalated.case_id)]
    assert kinds[-2:] == ["escalated", "final_action"]
    stack.close_open_cases()

    approval = stack.send("martin", "Quiero el reembolso del pedido 1005")
    events = reconstruct_case(stack.conn, stack.seed.tenant_id, approval.case_id)
    tool_events = [e.payload for e in events if e.kind == "tool_call"]
    assert tool_events[-1]["status"] == "approval_required"
    assert tool_events[-1]["approval_id"] == str(approval.approval_id)
    assert verify_chain(stack.conn, stack.seed.tenant_id, approval.case_id).ok
