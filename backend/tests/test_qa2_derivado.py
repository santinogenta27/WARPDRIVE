"""Hito QA 2 — test de aceptación: un caso ya derivado no repite la misma frase (decisión 097).

En las simulaciones con el modelo real, un cliente que seguía escribiendo después de la derivación
recibía cuatro veces seguidas "Te paso con una persona del equipo…".
"""

import psycopg
from simulated.stack import build_simulated_stack

from app import i18n


def test_el_segundo_mensaje_dice_que_ya_lo_paso(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    first = stack.send("lucia", "Quiero hablar con una persona")
    assert first.action == "derivar"
    assert first.message.startswith(i18n.text("es", "escalation"))
    again = stack.send("lucia", "¿Hola? ¿Hay alguien? Quiero hablar con una persona")
    assert again.action == "derivar" and again.case_id == first.case_id
    assert again.message.startswith(i18n.text("es", "escalation_waiting"))
    assert again.message != first.message


def test_en_ingles_tambien() -> None:
    assert "already passed your request" in i18n.text("en", "escalation_waiting")
