"""Hito 9, tarea 4 — test de aceptación: reglas de atención en el panel, sin código (decisión 051).

Herramientas permitidas, montos máximos y aprobación ya se editaban (decisión 026). Esta tarea
suma, desde el panel:

- exigir (o no) identidad verificada por herramienta, y siempre pedir aprobación;
- horario de atención: fuera de horario, quien deriva le dice al cliente cuándo le responden;
- a quién derivar: el email que recibe los casos derivados.

Todo lo aplica el código (guardián y ciclo), nunca el prompt.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.companies import review, rules
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

BA = ZoneInfo("America/Argentina/Buenos_Aires")
ACME_EMAIL, ACME_PASSWORD = "operador@plataforma.example", "clave-del-operador"


def test_el_horario_se_valida_y_se_aplica_en_la_hora_de_buenos_aires() -> None:
    hours = rules.parse_hours("1,2,3,4,5", "09:00", "18:00")
    monday_noon = datetime(2026, 11, 16, 12, 0, tzinfo=BA)
    monday_night = datetime(2026, 11, 16, 22, 0, tzinfo=BA)
    sunday = datetime(2026, 11, 15, 12, 0, tzinfo=BA)
    assert rules.is_open(hours, monday_noon)
    assert not rules.is_open(hours, monday_night) and not rules.is_open(hours, sunday)
    assert rules.is_open(None, sunday)  # sin horario cargado, siempre atiende
    with pytest.raises(rules.RulesError):
        rules.parse_hours("1", "18:00", "09:00")
    with pytest.raises(rules.RulesError):
        rules.parse_hours("", "09:00", "18:00")
    assert "lunes a viernes" in rules.describe(hours, "es")
    with pytest.raises(rules.RulesError):
        rules.parse_handoff_email("no-es-un-email")


def test_fuera_de_horario_la_derivacion_dice_cuando_responden(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack: SimulatedStack = build_simulated_stack(conn)
    tenant = stack.seed.tenant_id
    rules.save(conn, tenant, rules.parse_hours("1,2,3,4,5", "09:00", "18:00"), None)
    monkeypatch.setattr(rules, "_now", lambda: datetime(2026, 11, 15, 12, 0, tzinfo=BA))
    result = stack.send("lucia", "Quiero hablar con una persona")
    assert result.action == "derivar"
    assert "lunes a viernes de 09:00 a 18:00" in result.message


def test_la_identidad_por_herramienta_la_aplica_el_guardian(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    tenant = stack.seed.tenant_id
    lookup = next(t for t in review.tools(conn, tenant) if t.name == "consultar_pedido")
    assert lookup.manifest["requires_verified_contact"]
    review.set_tool(conn, tenant, lookup.id, requires_verified=False)
    tool = next(t for t in review.tools(conn, tenant) if t.name == "consultar_pedido")
    assert tool.manifest["requires_verified_contact"] is False
    refund = next(t for t in review.tools(conn, tenant) if t.name == "iniciar_reembolso")
    review.set_tool(conn, tenant, refund.id, always_approval=True)
    tool = next(t for t in review.tools(conn, tenant) if t.name == "iniciar_reembolso")
    assert tool.manifest["approval_over"] == 0


@pytest.fixture
def acme(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setenv("PANEL_EMAIL", ACME_EMAIL)
    monkeypatch.setenv("PANEL_PASSWORD", ACME_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield stack.seed.tenant_id
    finally:
        app.dependency_overrides.clear()


def test_el_panel_guarda_horario_y_a_quien_derivar(
    conn: psycopg.Connection, acme: uuid.UUID
) -> None:
    client = TestClient(app)
    client.post("/panel/login", data={"email": ACME_EMAIL, "password": ACME_PASSWORD})
    page = client.get("/panel/agent").text
    assert "Horario y derivaciones" in page and "Exige identidad verificada" in page
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    response = client.post(
        "/panel/configuracion/reglas",
        data={
            "csrf": token.group(1),
            **{f"dia_{d}": "si" for d in range(1, 6)},
            "desde": "09:00",
            "hasta": "18:00",
            "derivar_a": "soporte@acme.example",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    found = rules.load(conn, acme)
    assert found.hours is not None and found.hours.days == (1, 2, 3, 4, 5)
    assert found.handoff_email == "soporte@acme.example"
