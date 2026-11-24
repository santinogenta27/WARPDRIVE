"""Hito 9, tarea 10 — test de aceptación: registro self-service e invitaciones (decisión 058).

- Una empresa se registra sola: nombre, email y clave. Recién al abrir el enlace del email se
  crea, con ese usuario como admin. El enlace sirve una vez.
- Un admin invita a un compañero como admin o persona del equipo. La persona del equipo atiende
  Casos y Aprobaciones, pero no entra a Configuración ni a Onboarding.
"""

import re
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.companies import signup
from app.mailer import Email
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


def _token(email: Email) -> str:
    found = re.search(r"token=([\w-]+)", email.body)
    assert found is not None
    return found.group(1)


def test_el_registro_crea_la_empresa_recien_al_confirmar_el_email(
    conn: psycopg.Connection,
) -> None:
    box = _Outbox()
    signup.start_signup(
        conn,
        box,
        company="Tienda Sur",
        email="Dueña@TiendaSur.example",
        password="clave-de-tienda-sur",
        base_url="https://panel.example",
    )
    assert conn.execute("select 1 from tenants where name = 'Tienda Sur'").fetchone() is None
    assert box.sent[0].to == "dueña@tiendasur.example"
    assert "https://panel.example/registro/confirmar?token=" in box.sent[0].body
    created = signup.confirm_signup(conn, _token(box.sent[0]))
    assert created.name == "Tienda Sur"
    user = auth.company_login(conn, "dueña@tiendasur.example", "clave-de-tienda-sur")
    assert user is not None and user.tenant_id == created.tenant_id
    with pytest.raises(signup.SignupError):
        signup.confirm_signup(conn, _token(box.sent[0]))  # una sola vez


def test_el_registro_valida_los_datos(conn: psycopg.Connection) -> None:
    for data in (
        {"company": "X", "email": "a@b.example", "password": "clave-larga-123"},
        {"company": "Tienda", "email": "no-es-email", "password": "clave-larga-123"},
        {"company": "Tienda", "email": "a@b.example", "password": "corta"},
    ):
        with pytest.raises(signup.SignupError):
            signup.start_signup(conn, _Outbox(), base_url="https://p.example", **data)


@pytest.fixture
def panel(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Outbox]:
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    auth._failures.clear()
    box = _Outbox()
    monkeypatch.setattr("app.panel.team_routes.mailer_from_env", lambda: box)
    monkeypatch.setattr("app.panel.signup_routes.mailer_from_env", lambda: box)
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield box
    finally:
        app.dependency_overrides.clear()


def _csrf(client: TestClient, path: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert found is not None
    return found.group(1)


def test_de_punta_a_punta_registro_invitacion_y_roles(
    conn: psycopg.Connection, panel: _Outbox
) -> None:
    public = TestClient(app)
    assert "Creá la cuenta de tu empresa" in public.get("/registro").text
    response = public.post(
        "/registro",
        data={
            "empresa": "Tienda Sur",
            "email": "duena@tiendasur.example",
            "clave": "clave-sur-123",
        },
    )
    assert response.status_code == 200 and "Revisá tu email" in response.text
    response = public.get(
        f"/registro/confirmar?token={_token(panel.sent[-1])}", follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/panel/home"

    owner = TestClient(app)
    owner.post(
        "/panel/login", data={"email": "duena@tiendasur.example", "password": "clave-sur-123"}
    )
    assert "Equipo" in owner.get("/panel/settings").text
    owner.post(
        "/panel/equipo/invitar",
        data={
            "csrf": _csrf(owner, "/panel/settings"),
            "email": "beto@tiendasur.example",
            "rol": "agent",
        },
    )
    invitation = panel.sent[-1]
    assert invitation.to == "beto@tiendasur.example"

    agent = TestClient(app)
    page = agent.get(f"/panel/invitacion?token={_token(invitation)}").text
    assert "Elegí tu clave" in page
    response = agent.post(
        "/panel/invitacion",
        data={"token": _token(invitation), "clave": "clave-de-beto-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert agent.get("/panel/inbox").status_code == 200
    assert agent.get("/panel/inbox/approvals").status_code == 200
    assert agent.get("/panel/configuracion").status_code == 403
    assert agent.get("/panel/knowledge").status_code == 403
    assert agent.get("/panel/settings").status_code == 403
