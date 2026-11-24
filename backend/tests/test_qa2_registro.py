"""Hito QA 2 — test de aceptación: el email de confirmación en el idioma del registro (096).

En el recorrido de punta a punta, la página de registro estaba en inglés (el panel sale en inglés
por defecto, decisión 085) y el email de confirmación llegaba en español.
"""

from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.mailer import Email
from app.main import app
from app.panel import auth, english
from app.panel.routes import LANG_COOKIE, PanelDeps, panel_deps


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


@pytest.fixture
def box(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Outbox]:
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")
    found = _Outbox()
    monkeypatch.setattr("app.panel.signup_routes.mailer_from_env", lambda: found)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn), tenant_id=None, guardian=lambda c, t: None
    )
    try:
        yield found
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def _sign_up(client: TestClient, email: str) -> None:
    response = client.post(
        "/registro", data={"empresa": "Trail Co", "email": email, "clave": "clave-trail-123"}
    )
    assert response.status_code == 200, response.text


def test_registro_en_ingles_email_en_ingles(box: _Outbox) -> None:
    client = TestClient(app)
    assert "Create account" in client.get("/registro").text
    _sign_up(client, "owner@trail.example")
    email = box.sent[-1]
    assert email.subject == "Confirm your email to create Trail Co"
    assert "open this link (it expires in 24 hours)" in email.body
    assert "/registro/confirmar?token=" in email.body


def test_registro_en_espanol_email_en_espanol(box: _Outbox) -> None:
    client = TestClient(app, cookies={LANG_COOKIE: "es"})
    _sign_up(client, "duena@trail.example")
    assert box.sent[-1].subject == "Confirmá tu email para crear Trail Co"


def test_la_invitacion_sale_en_el_idioma_del_panel(conn: psycopg.Connection) -> None:
    from app.companies import signup
    from app.companies.create import create_company

    company = create_company(conn, "Trail Co", "owner@trail.example", "clave-trail-123")
    sent = _Outbox()
    signup.invite(conn, sent, company.tenant_id, company="Trail Co", email="ana@trail.example",
                  role="agent", base_url="https://p.example", language="en")  # fmt: skip
    assert sent.sent[-1].subject == "You're invited to the Trail Co dashboard"
    assert "as a team member" in sent.sent[-1].body
