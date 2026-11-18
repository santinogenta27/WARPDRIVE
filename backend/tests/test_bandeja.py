"""Hito 9, tarea 7 — test de aceptación: bandeja para humanos con aviso por email (decisión 054).

Los casos derivados y las aprobaciones pendientes ya estaban en el panel, con respuesta desde
ahí (decisión 022). Esta tarea suma el aviso por email al equipo:

- al email "a quién derivar" de las reglas o, si no hay, a los usuarios de la empresa;
- una sola vez por caso derivado y por aprobación;
- con el enlace al panel y sin el texto del cliente ni sus datos.
"""

import uuid
from typing import Any

import psycopg
import pytest
from simulated.stack import build_simulated_stack

from app.cases import team_notify
from app.companies import rules
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.mailer import Email


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> _Outbox:
    box = _Outbox()
    monkeypatch.setattr(team_notify, "BACKGROUND", False)
    monkeypatch.setattr(team_notify, "_mailer", lambda: box)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://panel.example")
    return box


def _user(conn: psycopg.Connection, tenant: uuid.UUID, email: str) -> None:
    with tenant_scope(conn, tenant):
        conn.execute(
            "insert into users (tenant_id, email, role) values (%s, %s, 'owner')"
            " on conflict do nothing",
            (tenant, email),
        )


def test_un_caso_derivado_avisa_una_vez_al_email_de_derivacion(
    conn: psycopg.Connection, outbox: _Outbox
) -> None:
    stack = build_simulated_stack(conn)
    rules.save(conn, stack.seed.tenant_id, None, "soporte@acme.example")
    result = stack.send("lucia", "Quiero hablar con una persona")
    assert result.action == "derivar"
    assert [e.to for e in outbox.sent] == ["soporte@acme.example"]
    email: Any = outbox.sent[0]
    assert f"https://panel.example/panel/casos/{result.case_id}" in email.body
    assert "Quiero hablar" not in email.body + email.subject  # nada de lo que escribió

    stack.send("lucia", "Quiero hablar con una persona ya")
    assert len(outbox.sent) == 1  # el mismo caso no avisa dos veces


def test_una_aprobacion_pendiente_avisa_con_el_enlace_a_aprobaciones(
    conn: psycopg.Connection, outbox: _Outbox
) -> None:
    stack = build_simulated_stack(conn)
    rules.save(conn, stack.seed.tenant_id, None, "soporte@acme.example")
    result = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert result.action == "pedir_aprobacion"
    assert [e.to for e in outbox.sent] == ["soporte@acme.example"]
    assert "https://panel.example/panel/aprobaciones" in outbox.sent[0].body


def test_sin_email_de_derivacion_avisa_a_los_usuarios_y_la_demo_a_nadie(
    conn: psycopg.Connection,
) -> None:
    company = create_company(conn, "Tienda Norte", "ana@tiendanorte.example", "clave-norte-123")
    _user(conn, company.tenant_id, "beto@tiendanorte.example")
    assert team_notify.recipients(conn, company.tenant_id) == [
        "ana@tiendanorte.example",
        "beto@tiendanorte.example",
    ]
    stack = build_simulated_stack(conn)  # Acme, la demo
    assert team_notify.recipients(conn, stack.seed.tenant_id) == []
