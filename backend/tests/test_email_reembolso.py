"""Demo en inglés, cambio B — test de aceptación del email de confirmación del reembolso
(decisión 046).

Cuando una persona aprueba en el panel el reembolso del guion y la herramienta se ejecuta, el
cliente verificado recibe un email con el monto, el medio de pago, el plazo y una referencia. En
el idioma de la empresa, una sola vez por aprobación y registrado en la auditoría. Si se rechaza,
no se envía nada.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent.loop import AgentResult
from app.cases import refund_confirmation
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

PASSWORD = "clave-del-panel-de-prueba"


@dataclass
class World:
    stack: SimulatedStack
    panel: TestClient


def _panel_user(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    email = f"ops-{uuid.uuid4().hex[:8]}@panel.example"
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into users (tenant_id, email, role, password_hash)"
            " values (%s, %s, 'owner', %s)",
            (tenant_id, email, auth.hash_password(PASSWORD)),
        )
    return email


def _world(conn: psycopg.Connection, company: str) -> Iterator[World]:
    stack = build_simulated_stack(conn, company=company)
    email = _panel_user(conn, stack.seed.tenant_id)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        mailer=lambda: stack.mailer,
    )
    panel = TestClient(app)
    response = panel.post(
        "/panel/login", data={"email": email, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    try:
        yield World(stack, panel)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def northwind(conn: psycopg.Connection) -> Iterator[World]:
    yield from _world(conn, "northwind")


@pytest.fixture
def acme(conn: psycopg.Connection) -> Iterator[World]:
    yield from _world(conn, "acme")


def _refund_waiting(stack: SimulatedStack, ask: str, email: str) -> AgentResult:
    """Un cliente nuevo del chat web pide el reembolso, se verifica por email y queda en
    revisión."""
    stack.add_contact("web")
    assert stack.send("web", ask).verification_requested
    stack.send("web", email)
    pending = stack.send("web", stack.last_code())
    assert pending.action == "pedir_aprobacion" and pending.approval_id is not None
    return pending


def _decide(world: World, approval_id: uuid.UUID, decision: str) -> str:
    # Configuración tiene formularios aunque no haya aprobaciones pendientes.
    page = world.panel.get("/panel/configuracion").text
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    response = world.panel.post(
        f"/panel/aprobaciones/{approval_id}",
        data={"decision": decision, "csrf": token.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location: str = response.headers["location"]
    return location


def _refund_emails(stack: SimulatedStack) -> list:  # type: ignore[type-arg]
    return [e for e in stack.mailer.sent if re.search("refund|reembolso", e.subject, re.I)]


def _kinds(stack: SimulatedStack, case_id: uuid.UUID) -> list[str]:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        rows = stack.conn.execute(
            "select kind from audit_events where case_id = %s order by seq", (case_id,)
        ).fetchall()
    return [row[0] for row in rows]


def test_al_aprobar_el_reembolso_del_guion_el_cliente_recibe_la_confirmacion(
    northwind: World,
) -> None:
    stack = northwind.stack
    pending = _refund_waiting(stack, "I'd like a refund for order NW-4528.", "emily@example.com")
    assert pending.approval_id is not None
    _decide(northwind, pending.approval_id, "aprobar")

    [email] = _refund_emails(stack)
    assert email.to == "emily@example.com"  # el email con el que se verificó
    assert email.subject == "Your refund is confirmed – order NW-4528"
    assert email.html is not None and '<html lang="en">' in email.html
    for fact in ("$213", "Visa ending in 4242", "5 to 7 business days", "R-NW-4528-1"):
        assert fact in email.body, fact
        assert fact in email.html, fact
    # El mismo estilo que el email del código: tablas y estilos en línea, sin imágenes.
    assert 'role="presentation"' in email.html and "<img" not in email.html
    assert "Northwind Outfitters" in email.html

    # En la auditoría, sin la dirección del cliente.
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        payload = stack.conn.execute(
            "select payload from audit_events where case_id = %s and kind = %s",
            (pending.case_id, refund_confirmation.SENT),
        ).fetchone()
    assert payload is not None
    assert payload[0]["approval_id"] == str(pending.approval_id)
    assert payload[0]["reference"] == "R-NW-4528-1"
    assert "emily" not in str(payload[0])


def test_aprobar_dos_veces_o_reintentar_no_manda_dos_emails(northwind: World) -> None:
    stack = northwind.stack
    pending = _refund_waiting(stack, "I'd like a refund for order NW-4528.", "emily@example.com")
    assert pending.approval_id is not None
    _decide(northwind, pending.approval_id, "aprobar")
    _decide(northwind, pending.approval_id, "aprobar")  # doble clic o reenvío del navegador
    # Reintentar el envío (por ejemplo, un job que se corre otra vez) tampoco lo repite.
    again = refund_confirmation.send(
        stack.conn,
        stack.mailer,
        stack.seed.tenant_id,
        pending.approval_id,
        {"ok": True, "data": {"id": "R-NW-4528-1", "amount": 213}},
    )
    assert again == "already_sent"
    assert len(_refund_emails(stack)) == 1
    assert _kinds(stack, pending.case_id).count(refund_confirmation.SENT) == 1


def test_si_se_rechaza_no_se_envia_nada(northwind: World) -> None:
    stack = northwind.stack
    pending = _refund_waiting(stack, "I'd like a refund for order NW-4528.", "emily@example.com")
    assert pending.approval_id is not None
    _decide(northwind, pending.approval_id, "rechazar")
    assert _refund_emails(stack) == []
    assert refund_confirmation.SENT not in _kinds(stack, pending.case_id)


def test_si_el_proveedor_falla_la_aprobacion_vale_y_queda_registrado(northwind: World) -> None:
    stack = northwind.stack
    pending = _refund_waiting(stack, "I'd like a refund for order NW-4528.", "emily@example.com")
    assert pending.approval_id is not None
    stack.mailer.failing = True
    _decide(northwind, pending.approval_id, "aprobar")
    kinds = _kinds(stack, pending.case_id)
    assert "approval_decided" in kinds and refund_confirmation.FAILED in kinds
    assert refund_confirmation.SENT not in kinds
    # Con el proveedor de vuelta, el reintento lo envía una vez.
    stack.mailer.failing = False
    result = {"ok": True, "data": {"id": "R-NW-4528-1", "amount": 213}}
    for _ in range(2):
        refund_confirmation.send(
            stack.conn, stack.mailer, stack.seed.tenant_id, pending.approval_id, result
        )
    assert len(_refund_emails(stack)) == 1


def test_el_email_queda_cifrado_en_la_verificacion(northwind: World) -> None:
    stack = northwind.stack
    _refund_waiting(stack, "I'd like a refund for order NW-4528.", "emily@example.com")
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        stored = stack.conn.execute(
            "select email_enc from identity_verifications where status = 'verified'"
        ).fetchall()
    assert stored and all(row[0] and "emily" not in row[0] for row in stored)


def test_en_espanol_para_una_empresa_en_espanol(acme: World) -> None:
    stack = acme.stack
    pending = _refund_waiting(stack, "Quiero el reembolso del pedido 1005", "martin@example.com")
    assert pending.approval_id is not None
    _decide(acme, pending.approval_id, "aprobar")

    [email] = _refund_emails(stack)
    assert email.to == "martin@example.com"
    assert email.subject == "Tu reembolso está confirmado – pedido 1005"
    assert email.html is not None and '<html lang="es">' in email.html
    # Sin medio de pago ni plazo en la respuesta del sistema de Acme: los textos generales.
    assert "tu medio de pago original" in email.body and "R-1005-1" in email.body
