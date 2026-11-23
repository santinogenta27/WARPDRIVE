"""Hito 6, etapa 2 — regla de reapertura de la spec y conteo para el cobro (decisión 027).

"Se cuenta como resuelto autónomamente si ningún humano intervino y no se reabre en 7 días. Esa
es la unidad de cobro." Si el cliente vuelve por el mismo tema dentro de los 7 días, el caso se
reabre y deja de contar. Aprobar un reembolso no es una intervención humana para el cobro.
"""

import uuid

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.audit import log as audit_log
from app.cases.billing import billing_summary
from app.db.connection import tenant_scope
from app.panel.notify import notify_customer
from app.policy.approvals import decide
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def case_row(stack: SimulatedStack, case_id: uuid.UUID) -> tuple[str, str | None, bool]:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select status, resolved_by, reopened from cases where id = %s", (case_id,)
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def close_days_ago(stack: SimulatedStack, case_id: uuid.UUID, days: int) -> None:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        stack.conn.execute(
            "update cases set closed_at = now() - make_interval(days => %s) where id = %s",
            (days, case_id),
        )


def resolved_order_case(stack: SimulatedStack) -> uuid.UUID:
    answered = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    stack.send("lucia", "¡Gracias!")
    assert case_row(stack, answered.case_id) == ("resolved", "agent", False)
    return answered.case_id


def test_si_el_cliente_vuelve_por_el_mismo_tema_el_caso_se_reabre(stack: SimulatedStack) -> None:
    case_id = resolved_order_case(stack)
    close_days_ago(stack, case_id, 3)
    calls_before = len(stack.claude_requests)

    back = stack.send("lucia", "Sigo sin recibir el pedido 1001, ¿dónde está?")

    assert back.case_id == case_id  # la conversación sigue en el mismo caso
    assert case_row(stack, case_id) == ("open", None, True)
    events = audit_log.reconstruct_case(stack.conn, stack.seed.tenant_id, case_id)
    assert "case_reopened" in [e.kind for e in events]
    assert audit_log.verify_chain(stack.conn, stack.seed.tenant_id, case_id).ok
    # Se clasifica una sola vez: 1 del clasificador + 2 del agente (herramienta y respuesta)
    # + 1 del supervisor (decisión 079).
    assert len(stack.claude_requests) - calls_before == 4

    # Aunque vuelva a resolverse sin una persona, ya no cuenta como resuelto por el asistente.
    stack.send("lucia", "Listo, gracias")
    assert case_row(stack, case_id) == ("resolved", "agent", True)
    assert billing_summary(stack.conn, stack.seed.tenant_id)["cobrables"] == 0


def test_otro_tema_o_pasados_los_7_dias_abre_un_caso_nuevo(stack: SimulatedStack) -> None:
    case_id = resolved_order_case(stack)
    close_days_ago(stack, case_id, 3)
    other_topic = stack.send("lucia", "¿Abren los domingos?")
    assert other_topic.case_id != case_id
    assert case_row(stack, case_id) == ("resolved", "agent", False)
    stack.send("lucia", "gracias")

    close_days_ago(stack, case_id, 8)
    late = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert late.case_id not in (case_id, other_topic.case_id)
    assert case_row(stack, case_id) == ("resolved", "agent", False)


def test_un_saludo_despues_de_resolver_no_reabre(stack: SimulatedStack) -> None:
    case_id = resolved_order_case(stack)
    hello = stack.send("lucia", "hola")
    assert hello.case_id != case_id
    assert case_row(stack, case_id) == ("resolved", "agent", False)


def test_el_conteo_para_el_cobro_sigue_la_regla_de_cierre(stack: SimulatedStack) -> None:
    tenant = stack.seed.tenant_id
    # 1. Resuelto por el agente hace más de 7 días, sin reabrir: se cobra.
    billable = resolved_order_case(stack)
    close_days_ago(stack, billable, 9)
    # 2. Resuelto por el agente hace 2 días: todavía puede reabrirse.
    recent = stack.send("martin", "¿Abren los domingos?").case_id
    stack.send("martin", "gracias")
    close_days_ago(stack, recent, 2)
    # 3. Reembolso aprobado por una persona: la aprobación no es una intervención humana.
    stack.add_contact("otra")
    stack.send("otra", "Quiero el reembolso del pedido 1005")
    stack.send("otra", "martin@example.com")
    refund = stack.send("otra", stack.last_code())
    assert refund.approval_id is not None
    guardian = Guardian(stack.conn, ToolRuntime(stack.acme))
    decide(stack.conn, guardian, tenant, refund.approval_id, approve=True)
    notify_customer(stack.conn, tenant, refund.case_id, "Aprobamos", kind="approval_decided")
    stack.send("otra", "¡Muchas gracias!")
    assert case_row(stack, refund.case_id) == ("resolved", "agent", False)
    close_days_ago(stack, refund.case_id, 10)
    # 4. Derivado y respondido por una persona: no se cobra.
    stack.add_contact("derivado")
    human = stack.send("derivado", "quiero hablar con una persona").case_id
    notify_customer(stack.conn, tenant, human, "Hola, soy Ana.")
    stack.send("derivado", "gracias")
    assert case_row(stack, human) == ("resolved", "human", False)
    close_days_ago(stack, human, 10)

    summary = billing_summary(stack.conn, tenant)
    assert summary == {
        "cobrables": 2,  # el 1 y el 3
        "en_ventana": 1,  # el 2: resuelto por el asistente, dentro de los 7 días
        "con_persona": 1,  # el 4
        "reabiertos": 0,
    }

    # El 2 se reabre: sale de la ventana y no se va a cobrar.
    stack.send("martin", "¿Y los feriados abren?")
    assert case_row(stack, recent) == ("open", None, True)
    after = billing_summary(stack.conn, tenant)
    assert (after["en_ventana"], after["reabiertos"], after["cobrables"]) == (0, 1, 2)
