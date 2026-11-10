"""Flujo central de la demo: un cliente nuevo pide un reembolso → verificación por código →
pedido de aprobación. Tiene que funcionar aunque el modelo pida el email en texto libre
(lo que hizo Groq en Render) o el cliente mande el email sin que se lo pidan."""

import re
import uuid

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent.loop import VERIFICATION_MESSAGE, VERIFIED_PREFIX, AgentResult
from app.contacts import hash_address
from app.db.connection import tenant_scope


def new_contact(stack: SimulatedStack) -> uuid.UUID:
    """Como una sesión nueva del chat web: contacto sin verificar."""
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (stack.seed.tenant_id, hash_address(f"web:test-{uuid.uuid4().hex}")),
        ).fetchone()
    assert row is not None
    contact_id: uuid.UUID = row[0]
    return contact_id


def send(stack: SimulatedStack, contact_id: uuid.UUID, text: str) -> AgentResult:
    return stack.agent.handle_message(stack.seed.tenant_id, contact_id, text)


def last_code(stack: SimulatedStack) -> str:
    match = re.search(r"\b\d{6}\b", stack.mailer.sent[-1].body)
    assert match is not None
    return match.group(0)


@pytest.mark.parametrize("mode", ["normal", "asks_contact"])
def test_reembolso_de_un_cliente_nuevo_verifica_por_codigo_y_pide_aprobacion(
    conn: psycopg.Connection, mode: str
) -> None:
    stack = build_simulated_stack(conn, mode=mode)
    contact = new_contact(stack)

    asked = send(stack, contact, "Quiero el reembolso del pedido 1005")
    # Siempre el flujo de verificación por código, nunca un pedido de datos en texto libre.
    assert asked.verification_requested and asked.message == VERIFICATION_MESSAGE
    assert "teléfono" not in asked.message

    sent = send(stack, contact, "martin@example.com")
    assert sent.verification_requested and stack.mailer.sent[-1].to == "martin@example.com"

    done = send(stack, contact, last_code(stack))
    assert done.message.startswith(VERIFIED_PREFIX)
    assert done.action == "pedir_aprobacion" and done.approval_id is not None


def test_el_email_que_manda_el_cliente_sin_que_se_lo_pidan_inicia_la_verificacion(
    conn: psycopg.Connection,
) -> None:
    stack = build_simulated_stack(conn)
    contact = new_contact(stack)

    sent = send(
        stack, contact, "Quiero el reembolso del pedido 1005, mi mail es martin@example.com"
    )
    assert sent.verification_requested and stack.mailer.sent[-1].to == "martin@example.com"

    done = send(stack, contact, last_code(stack))
    assert done.action == "pedir_aprobacion" and done.approval_id is not None
