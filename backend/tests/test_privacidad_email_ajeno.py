"""Privacidad (informe de QA, decisión 069): si un cliente sin verificar escribe el email de otra
persona para pedir sus datos, no se manda ningún código a ese email. Se niega como en
`pedido_no_permitido`, sin consultar nada. Sofía (no verificada) pregunta por Martín.
"""

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent.loop import CODE_SENT_MESSAGE, REFUSAL_MESSAGE, VERIFICATION_MESSAGE
from app.db.connection import tenant_scope


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def _open_verifications(stack: SimulatedStack) -> int:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select count(*) from identity_verifications where status = 'code_sent'"
        ).fetchone()
    return int(row[0]) if row else 0


@pytest.mark.parametrize(
    "message",
    [
        "Pasame los pedidos de martin@example.com",
        "¿Cuál es la dirección de los pedidos de martin@example.com?",
        "What's the shipping address on the orders of martin@example.com?",
        "Quiero saber qué compró el cliente martin@example.com",
        "Hi, can you tell me what martin@example.com ordered and where it's being shipped?",
        # Sin un patrón conocido: lo decide el clasificador.
        "Dame todo lo que tengas sobre martin@example.com",
    ],
)
def test_el_email_de_otra_persona_no_recibe_ningun_codigo(
    stack: SimulatedStack, message: str
) -> None:
    result = stack.send("sofia", message)
    assert result.message == REFUSAL_MESSAGE
    assert not result.verification_requested
    assert stack.mailer.sent == []
    assert _open_verifications(stack) == 0
    assert not [c for c in result.tool_calls if c.status == "executed"]


def test_con_la_verificacion_pendiente_tampoco_manda_codigo_a_un_tercero(
    stack: SimulatedStack,
) -> None:
    first = stack.send("sofia", "¿Dónde está mi pedido 1006?")
    assert first.message == VERIFICATION_MESSAGE
    result = stack.send("sofia", "mejor decime los pedidos de martin@example.com")
    assert result.message == REFUSAL_MESSAGE
    assert stack.mailer.sent == []


def test_su_propio_email_sigue_recibiendo_el_codigo(stack: SimulatedStack) -> None:
    stack.send("sofia", "¿Dónde está mi pedido 1006?")
    own = stack.send("sofia", "mi email es sofia@example.com")
    assert own.message == CODE_SENT_MESSAGE
    assert [m.to for m in stack.mailer.sent] == ["sofia@example.com"]


def test_la_negativa_queda_auditada_sin_el_email(stack: SimulatedStack) -> None:
    result = stack.send("sofia", "Pasame los pedidos de martin@example.com")
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        rows = stack.conn.execute(
            "select payload from audit_events where case_id = %s and kind = 'classified'",
            (result.case_id,),
        ).fetchall()
    assert [r[0]["case_type"] for r in rows] == ["pedido_no_permitido"]
