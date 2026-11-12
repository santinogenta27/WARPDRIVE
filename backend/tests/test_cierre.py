"""Regla de cierre de la spec ("Cierre"): un caso queda resuelto cuando el cliente confirma o
pasan 24 h sin respuesta. Es resuelto por el asistente si ninguna persona intervino (derivación
o respuesta desde el panel). Es la unidad de cobro."""

import uuid

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.cases.closing import CLOSING_MESSAGE, close_inactive, is_confirmation
from app.db.connection import tenant_scope
from app.panel.notify import notify_customer


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def case_row(stack: SimulatedStack, case_id: uuid.UUID) -> tuple[str, str | None, bool]:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select status, resolved_by, closed_at is not null from cases where id = %s",
            (case_id,),
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def last_audit(stack: SimulatedStack, case_id: uuid.UUID) -> str:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            # Sin la medición de tiempos, que cierra cada turno (decisión 025).
            "select kind from audit_events where case_id = %s and kind <> 'turn_timing'"
            " order by seq desc limit 1",
            (case_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def age(stack: SimulatedStack, case_id: uuid.UUID, hours: int) -> None:
    """Corre hacia atrás los mensajes del caso, como si hubieran pasado `hours` horas."""
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        stack.conn.execute(
            "update case_messages set created_at = created_at - make_interval(hours => %s)"
            " where case_id = %s",
            (hours, case_id),
        )


@pytest.mark.parametrize(
    "text", ["gracias", "Muchas gracias!", "listo, gracias", "perfecto", "eso era todo, gracias"]
)
def test_reconoce_cuando_el_cliente_confirma(text: str) -> None:
    assert is_confirmation(text)


@pytest.mark.parametrize("text", ["gracias, ¿y el pedido 1002?", "dale", "no", "hola"])
def test_no_toma_como_confirmacion_una_consulta_nueva(text: str) -> None:
    assert not is_confirmation(text)


def test_el_caso_se_resuelve_cuando_el_cliente_confirma(stack: SimulatedStack) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    thanks = stack.send("lucia", "¡Muchas gracias!")
    # Con el cierre va la pregunta de si se resolvió (decisión 033).
    assert thanks.case_id == answered.case_id and thanks.message.startswith(CLOSING_MESSAGE)
    assert case_row(stack, answered.case_id) == ("resolved", "agent", True)
    assert last_audit(stack, answered.case_id) == "case_resolved"
    # Lo siguiente que escribe es un caso nuevo.
    assert stack.send("lucia", "¿Tienen cuotas sin interés?").case_id != answered.case_id


def test_un_gracias_sin_respuesta_previa_no_cierra_nada(stack: SimulatedStack) -> None:
    first = stack.send("lucia", "gracias")
    assert case_row(stack, first.case_id)[0] == "open"


def test_un_caso_esperando_aprobacion_no_se_cierra_con_un_gracias(stack: SimulatedStack) -> None:
    pending = stack.send("martin", "Quiero el reembolso del pedido 1005")
    stack.send("martin", "gracias")
    assert case_row(stack, pending.case_id)[0] == "waiting_approval"


def test_pasadas_24_horas_sin_respuesta_del_cliente_el_caso_se_resuelve(
    stack: SimulatedStack,
) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    age(stack, answered.case_id, 23)
    assert close_inactive(stack.conn, stack.seed.tenant_id) == 0  # todavía no
    age(stack, answered.case_id, 2)
    assert close_inactive(stack.conn, stack.seed.tenant_id) == 1
    assert case_row(stack, answered.case_id) == ("resolved", "agent", True)


def test_un_derivado_se_cierra_solo_despues_de_que_responde_una_persona(
    stack: SimulatedStack,
) -> None:
    escalated = stack.send("martin", "quiero hablar con una persona")
    age(stack, escalated.case_id, 30)
    close_inactive(stack.conn, stack.seed.tenant_id)
    assert case_row(stack, escalated.case_id)[0] == "escalated"  # nadie le contestó todavía

    notify_customer(stack.conn, stack.seed.tenant_id, escalated.case_id, "Hola, soy Ana.")
    age(stack, escalated.case_id, 25)
    close_inactive(stack.conn, stack.seed.tenant_id)
    assert case_row(stack, escalated.case_id) == ("resolved", "human", True)


def test_si_el_ultimo_mensaje_es_del_cliente_el_caso_sigue_abierto(stack: SimulatedStack) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        stack.conn.execute(
            "insert into case_messages (tenant_id, case_id, role, text)"
            " values (%s, %s, 'customer', 'y los feriados?')",
            (stack.seed.tenant_id, answered.case_id),
        )
    age(stack, answered.case_id, 30)
    close_inactive(stack.conn, stack.seed.tenant_id)
    assert case_row(stack, answered.case_id)[0] == "open"
