"""Hito 7, tarea 3 — test de aceptación del CSAT (decisión 033).

Al cerrar un caso, el chat pregunta si la consulta se resolvió. El cliente responde sí o no, con
un comentario si quiere. La respuesta queda en el caso y se ve en Métricas. Un "no" reabre el
caso y lo pasa a una persona: no se cobra.
"""

import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.cases import billing, csat
from app.cases.closing import CLOSING_MESSAGE
from app.db.connection import tenant_scope
from app.main import app
from app.obs import metrics
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def closed_case(stack: SimulatedStack, who: str = "lucia") -> uuid.UUID:
    """Un caso que el asistente respondió y el cliente cerró con un "gracias"."""
    answered = stack.send(who, "¿Abren los domingos?")
    thanks = stack.send(who, "¡Muchas gracias!")
    assert thanks.case_id == answered.case_id
    return answered.case_id


def case_row(stack: SimulatedStack, case_id: uuid.UUID) -> tuple[str, str | None, bool]:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select status, resolved_by, reopened from cases where id = %s", (case_id,)
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def response(stack: SimulatedStack, case_id: uuid.UUID) -> tuple[bool | None, str | None, bool]:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select resolved, comment, answered_at is not null from csat_responses"
            " where case_id = %s",
            (case_id,),
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def count_cases(stack: SimulatedStack) -> int:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute("select count(*) from cases").fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sí", (True, "")),
        ("Si", (True, "")),
        ("SÍ!", (True, "")),
        ("si, gracias", (True, "gracias")),
        ("Sí, me respondieron rapidísimo", (True, "me respondieron rapidísimo")),
        ("no", (False, "")),
        ("No.", (False, "")),
        ("no, sigo sin saber si abren el feriado", (False, "sigo sin saber si abren el feriado")),
        ("No me sirvió la respuesta", (False, "No me sirvió la respuesta")),
        ("👍", (True, "")),
        ("👎", (False, "")),
    ],
)
def test_reconoce_la_respuesta_del_cliente(text: str, expected: tuple[bool, str]) -> None:
    assert csat.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "si compro hoy, ¿cuándo llega?",  # "si" condicional: es otra consulta
        "¿Tienen cuotas sin interés?",
        "nosotros compramos ayer",
        "sino llega mañana lo cancelo",
        "",
    ],
)
def test_una_consulta_nueva_no_es_una_respuesta(text: str) -> None:
    assert csat.parse(text) is None


def test_al_cerrar_el_caso_el_chat_pregunta_si_se_resolvio(stack: SimulatedStack) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    assert csat.QUESTION not in answered.message  # mientras el caso está abierto no pregunta
    thanks = stack.send("lucia", "¡Muchas gracias!")
    assert thanks.message.startswith(CLOSING_MESSAGE) and thanks.message.endswith(csat.QUESTION)
    assert case_row(stack, answered.case_id) == ("resolved", "agent", False)
    assert response(stack, answered.case_id) == (None, None, False)  # preguntado, sin respuesta


def test_un_si_queda_registrado_y_el_caso_sigue_resuelto(stack: SimulatedStack) -> None:
    case_id = closed_case(stack)
    before = count_cases(stack)
    reply = stack.send("lucia", "Sí, me respondieron rapidísimo")
    assert reply.case_id == case_id and reply.message == csat.THANKS
    assert reply.action == "responder"
    assert response(stack, case_id) == (True, "me respondieron rapidísimo", True)
    assert case_row(stack, case_id) == ("resolved", "agent", False)  # se sigue cobrando
    assert count_cases(stack) == before  # la respuesta no abre un caso nuevo
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        audited = stack.conn.execute(
            "select payload from audit_events where case_id = %s and kind = 'csat_answered'",
            (case_id,),
        ).fetchone()
    assert audited is not None and audited[0] == {"resuelto": True, "con_comentario": True}
    # Ya respondió: lo próximo que escribe es una consulta nueva, aunque empiece con "sí".
    after = stack.send("lucia", "¿Tienen cuotas sin interés?")
    assert after.case_id != case_id and csat.THANKS not in after.message


def test_un_no_reabre_el_caso_y_lo_pasa_a_una_persona(stack: SimulatedStack) -> None:
    case_id = closed_case(stack)
    tenant = stack.seed.tenant_id
    assert billing.billing_summary(stack.conn, tenant)["en_ventana"] == 1
    reply = stack.send("lucia", "No, necesito saber si abren el feriado del lunes")
    assert reply.case_id == case_id and reply.action == "derivar"
    assert reply.message.startswith(csat.SORRY)
    assert response(stack, case_id) == (False, "necesito saber si abren el feriado del lunes", True)
    assert case_row(stack, case_id) == ("escalated", None, True)
    # Un caso que el cliente dice que no se resolvió no se cobra.
    summary = billing.billing_summary(stack.conn, tenant)
    assert summary["en_ventana"] == 0 and summary["reabiertos"] == 1
    assert reply.escalation_summary is not None
    assert "no se resolvió" in reply.escalation_summary["motivo"]


def test_si_no_responde_la_pregunta_sigue_el_flujo_normal(stack: SimulatedStack) -> None:
    case_id = closed_case(stack)
    other = stack.send("lucia", "¿Tienen cuotas sin interés?")
    assert other.case_id != case_id and other.action == "responder"
    assert response(stack, case_id) == (None, None, False)
    # La pregunta vence: un "sí" suelto al día siguiente ya no es una respuesta al CSAT.
    stack.send("lucia", "gracias")  # cierra el segundo caso, que pregunta de nuevo
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        stack.conn.execute("update csat_responses set asked_at = asked_at - interval '25 hours'")
    assert csat.pending(stack.conn, stack.seed.tenant_id, stack.seed.contacts["lucia"]) is None


def test_la_respuesta_de_un_cliente_no_toca_el_caso_de_otro(stack: SimulatedStack) -> None:
    lucia = closed_case(stack, "lucia")
    martin = closed_case(stack, "martin")
    stack.send("martin", "no")
    assert response(stack, lucia) == (None, None, False)
    assert response(stack, martin) == (False, None, True)
    assert case_row(stack, lucia)[0] == "resolved"


def test_un_caso_ya_preguntado_no_vuelve_a_preguntar(stack: SimulatedStack) -> None:
    case_id = closed_case(stack)
    stack.send("lucia", "no")  # lo reabre y lo deriva
    again = stack.send("lucia", "listo, gracias")  # el cliente lo cierra de nuevo
    assert again.case_id == case_id and again.message == CLOSING_MESSAGE
    assert response(stack, case_id) == (False, None, True)  # queda la primera respuesta


def test_metricas_muestra_el_csat(stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, tenant = stack.conn, stack.seed.tenant_id
    closed_case(stack, "lucia")
    stack.send("lucia", "sí, muy claro todo")
    closed_case(stack, "martin")
    stack.send("martin", "No, me faltó el horario del sábado")
    closed_case(stack, "sofia")  # preguntado, sin respuesta

    data = metrics.collect(conn, tenant)["csat"]
    assert data["preguntas"] == 3 and data["respuestas"] == 2
    assert data["si"] == 1 and data["no"] == 1 and data["satisfaccion"] == 0.5
    assert sorted(c["comentario"] for c in data["comentarios"]) == [
        "me faltó el horario del sábado",
        "muy claro todo",
    ]

    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=tenant,
        guardian=lambda c, t: stack.agent._guardian,
    )
    try:
        client = TestClient(app)
        client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD})
        page = client.get("/panel/metricas").text
    finally:
        app.dependency_overrides.clear()
    assert "Satisfacción" in page and "50%" in page
    assert "me faltó el horario del sábado" in page and "2 de 3" in page

    # Otra empresa no ve estas respuestas.
    row = conn.execute("insert into tenants (name) values ('Otra') returning id").fetchone()
    assert row is not None
    assert metrics.collect(conn, row[0])["csat"]["preguntas"] == 0


def test_la_auditoria_del_caso_muestra_la_respuesta(
    stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = closed_case(stack)
    stack.send("lucia", "sí")
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(stack.conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
    )
    try:
        client = TestClient(app)
        client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD})
        page = client.get(f"/panel/casos/{case_id}").text
    finally:
        app.dependency_overrides.clear()
    assert "El cliente dijo que su consulta se resolvió" in page


@pytest.fixture(autouse=True)
def _clean_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()
