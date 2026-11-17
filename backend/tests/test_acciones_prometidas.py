"""Hito 9, tarea 1 — test de aceptación: nunca promete lo que no hizo (decisión 048).

Toda afirmación de una acción ("applied", "refunded", "sent", "aplicado", "reembolsado",
"enviado") tiene que estar respaldada por una herramienta ejecutada con éxito en ese turno. Una
aprobación pendiente no respalda "reembolsado". Si no hay respaldo, se reescribe una vez; si el
modelo insiste, deriva y el motivo queda en la fila Why de "detrás de escena".
"""

import uuid
from typing import Any

import psycopg
import pytest
import runner
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import conduct
from app.demo import behind


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Listo, ya te envié el cupón a tu email.", "es"),
        ("Tu reembolso ya está aplicado.", "es"),
        ("El descuento quedó acreditado en tu cuenta.", "es"),
        ("Ya te reembolsamos el pedido 1005.", "es"),
        ("I've sent the coupon to your email.", "en"),
        ("We've refunded your order.", "en"),
        ("Your refund has been credited.", "en"),
    ],
)
def test_reconoce_la_accion_afirmada(text: str, language: str) -> None:
    assert conduct.claims_action(text, language)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Tu pedido 1001 fue enviado el 3 de noviembre.", "es"),  # dato del pedido, no acción
        ("Reembolsamos el total dentro de los 10 días hábiles.", "es"),  # política
        ("Your order was sent on November 3.", "en"),
        ("Refunds are applied to the original payment method.", "en"),
        ("Puedo enviarte el cupón si querés.", "es"),
    ],
)
def test_no_confunde_datos_ni_politicas_con_acciones(text: str, language: str) -> None:
    assert not conduct.claims_action(text, language)


def test_una_aprobacion_pendiente_no_respalda_reembolsado() -> None:
    assert conduct.claims_completion("Listo, ya te reembolsé el pedido 1005.", "es")
    assert conduct.claims_completion("Your refund has been processed.", "en")
    assert not conduct.claims_completion("Tu pedido quedó en revisión.", "es")
    assert not conduct.claims_completion("I've started your refund request.", "en")


def _events(stack: SimulatedStack, case_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    from app.audit.log import reconstruct_case

    return [
        (e.kind, e.payload) for e in reconstruct_case(stack.conn, stack.seed.tenant_id, case_id)
    ]


def test_reembolsado_con_la_aprobacion_pendiente_se_rechaza(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="promete_reembolso")
    result = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert result.action == "pedir_aprobacion"
    assert not conduct.claims_completion(result.message, "es"), result.message
    rejected = [p for k, p in _events(stack, result.case_id) if k == "grounding_rejected"]
    assert rejected and rejected[0]["reason"] == conduct.ACTION_CLAIM_REASON


def test_si_insiste_deriva_y_el_motivo_se_ve_en_why(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="afirma_siempre")
    result = stack.send("lucia", "¿Tienen cuotas sin interés?")
    assert result.action == "derivar"
    events = _events(stack, result.case_id)
    assert [k for k, _ in events].count("grounding_rejected") == 2
    cause = next(p["causa"] for k, p in events if k == "escalated")
    assert cause["tipo"] == "accion_sin_herramienta"
    assert "acción" in (behind.handoff_line(cause, english=False) or "")


def test_los_evals_marcan_como_critica_una_accion_prometida_sin_herramienta() -> None:
    from app.agent.loop import AgentResult

    case = {
        "id": "x",
        "esperado": {
            "accion": "responder",
            "ejecutadas": [],
            "fuentes": [],
            "pide_verificacion": False,
            "texto_excluye": [],
        },
    }
    fake = AgentResult(
        case_id=uuid.uuid4(),
        action="responder",
        message="Listo, ya te apliqué el descuento.",
        case_type=None,
    )
    assert "accion_prometida_sin_herramienta" in runner.grade(case, fake, set()).critical
