"""Botón "Reiniciar demo" del panel: deja el panel limpio para empezar cada demo.

La auditoría es de solo inserción (también para el dueño de la base), así que los casos no se
borran: se archivan. Salen del panel y del agente, pero conservan su cadena de auditoría
(decisión 024)."""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.audit.log import verify_chain
from app.db.connection import tenant_scope
from app.main import app
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime

EMAIL, PASSWORD = "demo@acme.example", "clave-de-demo"


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


@pytest.fixture
def resets() -> list[str]:
    return []


@pytest.fixture
def client(
    conn: psycopg.Connection,
    stack: SimulatedStack,
    resets: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("PANEL_EMAIL", EMAIL)
    monkeypatch.setenv("PANEL_PASSWORD", PASSWORD)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c: Guardian(c, ToolRuntime(stack.acme)),
        report_tenant=stack.seed.tenant_id,
        reset_sandbox=lambda: resets.append("acme"),
    )
    try:
        test_client = TestClient(app)
        test_client.post("/panel/login", data={"email": EMAIL, "password": PASSWORD})
        yield test_client
    finally:
        app.dependency_overrides.clear()


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_reiniciar_la_demo_deja_el_panel_limpio_sin_tocar_la_auditoria(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack, resets: list[str]
) -> None:
    pending = stack.send("martin", "Quiero el reembolso del pedido 1005")
    stack.close_open_cases()
    escalated = stack.send("lucia", "quiero hablar con una persona")
    assert "Reiniciar demo" in client.get("/panel/casos").text

    confirm = client.get("/panel/reiniciar").text  # pide confirmar antes
    assert "no se puede deshacer" not in confirm and "archiv" in confirm
    forged = client.post("/panel/reiniciar", data={"csrf": "x"}, follow_redirects=False)
    assert forged.status_code == 403
    done = client.post("/panel/reiniciar", data={"csrf": csrf(confirm)}, follow_redirects=False)
    assert done.status_code == 303 and resets == ["acme"]

    assert "Todavía no hay conversaciones" in client.get("/panel/casos").text
    assert "No hay pedidos esperando aprobación" in client.get("/panel/aprobaciones").text
    assert client.get(f"/panel/casos/{escalated.case_id}").status_code == 404
    for case_id in (pending.case_id, escalated.case_id):
        assert verify_chain(conn, stack.seed.tenant_id, case_id).ok  # la auditoría sigue entera
    with tenant_scope(conn, stack.seed.tenant_id):
        row = conn.execute(
            "select status from approvals where id = %s", (pending.approval_id,)
        ).fetchone()
    assert row is not None and row[0] == "rejected"  # una aprobación archivada no se ejecuta

    # El cliente que vuelve a escribir empieza un caso nuevo, no el archivado.
    again = stack.send("lucia", "¿Abren los domingos?")
    assert again.case_id != escalated.case_id and isinstance(again.case_id, uuid.UUID)
