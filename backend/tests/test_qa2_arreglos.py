"""Hito QA 2 — tests de aceptación de lo que el QA encontró con el modelo real.

* Decisión 091: con el reembolso negado por el guardián, el agente no puede decir que "quedó en
  revisión" ni que "iniciamos el proceso": no hay ninguna aprobación que lo respalde.
"""

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import conduct
from app.companies import review
from app.db.connection import tenant_scope

REFUND = "Quiero el reembolso del pedido 1005"


@pytest.mark.parametrize(
    "text",
    [
        "Iniciamos el proceso de reembolso de los $35.000, el cual queda en revisión por nuestro "
        "equipo.",
        "El reembolso está en revisión por nuestro equipo y se acreditará en 5 a 10 días.",
        "Tu pedido quedó pendiente de aprobación.",
    ],
)
def test_detecta_revision_afirmada_en_espanol(text: str) -> None:
    assert conduct.claims_review(text, "es")


@pytest.mark.parametrize(
    "text",
    [
        "Your refund is under review by our team.",
        "We've submitted your refund request.",
        "It has been sent for approval.",
    ],
)
def test_detecta_revision_afirmada_en_ingles(text: str) -> None:
    assert conduct.claims_review(text, "en")


@pytest.mark.parametrize(
    "text",
    [
        "Reembolsamos el total en 5 a 10 días hábiles desde que recibimos el producto.",
        "Tu reembolso sigue en revisión: te avisamos apenas una persona lo apruebe.",
        "Para iniciar un reembolso necesitamos el número de pedido.",
        "No pude completar el reembolso: supera el monto máximo.",
    ],
)
def test_no_confunde_politicas_ni_estados_de_antes(text: str) -> None:
    assert not conduct.claims_review(text, "es")


def _capped(stack: SimulatedStack) -> None:
    """El reembolso con un tope de 1000: el del pedido 1005 (35.000) se niega."""
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute("select id from tools where name = 'iniciar_reembolso'").fetchone()
    assert row is not None
    review.set_tool(stack.conn, stack.seed.tenant_id, row[0], max_amount="1000",
                    approval_over="0")  # fmt: skip


def _rejected_reasons(stack: SimulatedStack) -> list[str]:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        rows = stack.conn.execute(
            "select payload ->> 'reason' from audit_events where kind = 'grounding_rejected'"
        ).fetchall()
    return [r[0] for r in rows]


def test_reembolso_negado_no_queda_en_revision(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="revision_sin_aprobacion")
    _capped(stack)
    result = stack.send("martin", REFUND)
    assert [t.status for t in result.tool_calls if t.name == "iniciar_reembolso"] == ["denied"]
    assert "revisión" not in result.message, result.message
    assert conduct.ACTION_CLAIM_REASON in _rejected_reasons(stack)


def test_si_insiste_deriva(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="revision_sin_aprobacion_siempre")
    _capped(stack)
    result = stack.send("martin", REFUND)
    assert result.action == "derivar"
    assert "revisión" not in result.message, result.message


def test_con_la_aprobacion_creada_si_puede_decir_que_quedo_en_revision(
    conn: psycopg.Connection,
) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("martin", REFUND)
    assert result.action == "pedir_aprobacion"
    assert "quedó en revisión" in result.message
    assert _rejected_reasons(stack) == []
