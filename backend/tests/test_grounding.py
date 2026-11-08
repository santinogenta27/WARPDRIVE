"""Tarea 9 — test de aceptación: pregunta sin fuente → deriva, no inventa.

El Claude simulado tiene modos de "modelo que se porta mal": inventa una respuesta, cita una
fuente que no recibió o mete un dato sin respaldo. El control de fundamento corre en código.
"""

import psycopg
from simulated.stack import build_simulated_stack

from app.agent.grounding import check_grounding
from app.db.connection import tenant_scope

INVENTED = "¡Sí, claro! Lo hacemos sin ningún problema y sin costo."


def _agent_messages(stack, case_id) -> list[str]:  # type: ignore[no-untyped-def]
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        rows = stack.conn.execute(
            "select text from case_messages where case_id = %s and role = 'agent'", (case_id,)
        ).fetchall()
    return [r[0] for r in rows]


def test_pregunta_sin_fuente_deriva_y_no_inventa(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="invent")
    result = stack.send("lucia", "¿Tienen cuotas sin interés con tarjetas extranjeras?")
    assert result.action == "derivar"
    assert INVENTED not in result.message
    assert all(INVENTED not in m for m in _agent_messages(stack, result.case_id))
    assert result.escalation_summary is not None
    assert "fuente" in result.escalation_summary["motivo"]


def test_una_respuesta_sin_fuente_se_reescribe_una_vez(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="invent_once")
    result = stack.send("lucia", "¿Tienen cuotas sin interés?")
    assert result.action == "responder"
    assert "medios_de_pago_y_cuotas" in result.sources
    assert INVENTED not in result.message


def test_fuente_que_no_estaba_en_los_documentos_no_vale(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="fake_source")
    result = stack.send("lucia", "¿Tienen cuotas sin interés?")
    assert result.action == "derivar"
    assert "politica_secreta" not in result.sources


def test_dato_sin_respaldo_aunque_no_se_declare_factual(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="sneaky_number")
    result = stack.send("lucia", "¿Tienen cuotas sin interés?")
    assert result.action == "derivar"
    assert "72" not in result.message


def test_la_derivacion_deja_resumen_para_el_humano(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("lucia", "¿Hacen arreglos de ruedo y costura?")
    assert result.action == "derivar"
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select status, escalation from cases where id = %s", (result.case_id,)
        ).fetchone()
    assert row is not None
    status, summary = row
    assert status == "escalated"
    assert set(summary) == {
        "intencion",
        "mensaje_cliente",
        "contacto_verificado",
        "pasos",
        "motivo",
    }
    assert summary["contacto_verificado"] is True


def test_reglas_del_control() -> None:
    docs = {"envios": "El envío es gratis en compras desde $80.000."}
    ok = check_grounding(
        "El envío es gratis desde $80.000.", ["envios"], True, docs, ["¿envío gratis?"], False
    )
    assert ok.ok
    no_source = check_grounding("Es gratis siempre.", [], True, docs, ["¿envío gratis?"], False)
    assert not no_source.ok
    from_tool = check_grounding(
        "Tu pedido 1001 llega el 10/11.",
        [],
        True,
        docs,
        ['{"estimated_delivery": "2026-11-10"}', "¿Dónde está mi pedido 1001?"],
        True,
    )
    assert from_tool.ok
    bad_number = check_grounding(
        "El envío es gratis desde $50.000.", ["envios"], True, docs, ["¿envío gratis?"], False
    )
    assert not bad_number.ok
    not_factual = check_grounding(
        "¿Me pasás el número de pedido?", [], False, docs, ["hola"], False
    )
    assert not_factual.ok
