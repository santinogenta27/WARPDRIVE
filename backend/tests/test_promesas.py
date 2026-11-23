"""Hito 12, tarea 7 — test de aceptación de las promesas cumplidas (decisión 084).

Si el agente promete algo a futuro ("te avisamos cuando…"), tiene que existir el aviso programado
que lo cumpla. Si no existe, no puede prometerlo.
"""

import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import promises
from app.audit.log import reconstruct_case
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth, english
from app.panel.notify import notify_customer
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"


def kinds(stack: SimulatedStack, case_id: uuid.UUID) -> list[str]:
    return [e.kind for e in reconstruct_case(stack.conn, stack.seed.tenant_id, case_id)]


def notices(stack: SimulatedStack, case_id: uuid.UUID) -> list[tuple[str, str]]:
    return [
        (n.kind, n.status) for n in promises.for_case(stack.conn, stack.seed.tenant_id, case_id)
    ]


@pytest.mark.parametrize(
    ("text", "promise"),
    [
        ("Te avisamos por acá cuando salga.", True),
        ("Le vamos a escribir apenas tengamos novedades.", True),
        ("Nos vamos a comunicar con vos mañana.", True),
        ("Te notificaremos el resultado.", True),
        ("We'll let you know as soon as it ships.", True),
        ("You'll get an email when it's ready.", True),
        ("Te avisamos que tu pedido ya salió.", False),  # informa ahora
        ("Avisanos si necesitás algo más.", False),
        ("Podés consultar el estado escribiendo de nuevo por acá.", False),
        ("We'll refund you the difference.", False),
        # Lo que hizo Gemini al reescribir: lo que la lleva al futuro, antes del verbo.
        ("Cuando eso pase, te enviamos un email con el número de seguimiento.", True),
    ],
)
def test_detecta_las_promesas_a_futuro(text: str, promise: bool) -> None:
    assert promises.promised(text) is promise


def test_un_documento_que_describe_el_aviso_de_la_tienda_lo_respalda() -> None:
    """El aviso lo manda el sistema de la tienda: citar ese documento respalda la promesa."""
    assert promises.documented(
        "Cuando el pedido se despacha, te enviamos por email el número de seguimiento."
    )
    assert promises.documented("Returns are free: we email you a prepaid return label.")
    assert not promises.documented("Hacemos envíos a todo el país en 48 horas hábiles.")


def test_sin_aviso_programado_no_puede_prometer(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="promete_aviso")
    result = stack.send("lucia", "¿Dónde está mi pedido 1002?")
    assert result.action == "responder"
    assert not promises.promised(result.message)  # se corrigió
    assert "promise_rejected" in kinds(stack, result.case_id)


def test_si_insiste_en_prometer_deriva(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="promete_aviso_siempre")
    result = stack.send("lucia", "¿Dónde está mi pedido 1002?")
    assert result.action == "derivar"
    [escalated] = [
        e.payload
        for e in reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id)
        if e.kind == "escalated"
    ]
    assert escalated["causa"] == {"tipo": "promesa_sin_aviso"}


def test_con_una_aprobacion_la_promesa_tiene_su_aviso(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert result.action == "pedir_aprobacion" and result.approval_id is not None
    assert promises.promised(result.message)  # "te avisamos por acá apenas esté"
    assert "promise_rejected" not in kinds(stack, result.case_id)
    assert notices(stack, result.case_id) == [(promises.APPROVAL, "pending")]
    # Al decidir la aprobación, el aviso le llega al cliente y queda cumplido.
    notify_customer(
        stack.conn,
        stack.seed.tenant_id,
        result.case_id,
        "Tu reembolso fue aprobado.",
        kind="approval_decided",
        detail={"approval_id": str(result.approval_id)},
    )
    assert notices(stack, result.case_id) == [(promises.APPROVAL, "sent")]
    assert "notice_sent" in kinds(stack, result.case_id)


def test_un_aviso_pendiente_respalda_la_promesa_de_otro_turno(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="promete_aviso_siempre")
    first = stack.send("lucia", "¿Dónde está mi pedido 1002?")
    assert first.action == "derivar"  # prometió dos veces: se derivó (y se programó el aviso)
    assert notices(stack, first.case_id) == [(promises.TEAM, "pending")]
    again = stack.send("lucia", "¿Dónde está mi pedido 1002?")
    assert again.case_id == first.case_id
    assert again.action == "responder"  # el equipo le va a responder: la promesa vale


def test_derivar_programa_la_respuesta_del_equipo(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("lucia", "Quiero hablar con una persona")
    assert notices(stack, result.case_id) == [(promises.TEAM, "pending")]
    notify_customer(stack.conn, stack.seed.tenant_id, result.case_id, "Hola, soy Ana.")
    assert notices(stack, result.case_id) == [(promises.TEAM, "sent")]


def test_si_se_cancela_la_aprobacion_se_cancela_el_aviso(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("martin", "Quiero el reembolso del pedido 1005")
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        stack.conn.execute(
            "update approvals set status = 'rejected' where id = %s", (result.approval_id,)
        )
    assert promises.pending(stack.conn, stack.seed.tenant_id, result.case_id) == []
    assert notices(stack, result.case_id) == [(promises.APPROVAL, "cancelled")]


def test_programar_es_idempotente(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("lucia", "Quiero hablar con una persona")
    again = promises.schedule(stack.conn, stack.seed.tenant_id, result.case_id, promises.TEAM)
    assert again is False
    assert len(notices(stack, result.case_id)) == 1


@pytest.fixture
def panel(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:  # type: ignore[type-arg]
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
    )
    client = TestClient(app)
    client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD})
    try:
        yield stack, client
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def test_el_caso_muestra_los_avisos_programados(panel: tuple) -> None:  # type: ignore[type-arg]
    stack, client = panel
    result = stack.send("martin", "Quiero el reembolso del pedido 1005")
    page = client.get(f"/panel/casos/{result.case_id}").text
    assert "Avisos programados" in page
    assert "Avisarle al cliente cuando se decida la aprobación" in page and "Pendiente" in page
