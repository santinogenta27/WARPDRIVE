"""Hito 9 — de punta a punta: una empresa nueva, sola, desde el registro hasta atender en su sitio.

Registro con confirmación por email → sube su política → fija reglas y dominio → prueba en el
playground → sale en vivo cuando el reporte pasa el umbral → el widget atiende en su sitio con
lo que dice su documento → el caso aparece en el panel y en Métricas.

El análisis del historial (que usa el modelo) no se corre acá: se carga su reporte, como lo deja
el pipeline. El resto es el código de producción con el modelo simulado.
"""

import json
import re
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.web.routes import WebDeps, web_deps
from app.db.connection import tenant_scope
from app.mailer import Email
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

SHOP = {"Origin": "https://www.tiendasur.example"}
POLICY = "# Política de despacho\n\nEl envío tarda 72 horas hábiles a todo el país.\n".encode()


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


def _events(response: Any) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        found.append((fields["event"], json.loads(fields["data"])))
    return found


@pytest.fixture
def world(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[SimulatedStack, _Outbox]]:
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    auth._failures.clear()
    box = _Outbox()
    monkeypatch.setattr("app.panel.signup_routes.mailer_from_env", lambda: box)
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent, address="web:demo"
    )
    try:
        yield stack, box
    finally:
        app.dependency_overrides.clear()


def _csrf(client: TestClient, path: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert found is not None, path
    return found.group(1)


def test_una_empresa_nueva_llega_sola_a_atender_en_su_sitio(
    conn: psycopg.Connection, world: tuple[SimulatedStack, _Outbox]
) -> None:
    _, box = world
    # 1. Se registra y confirma el email: entra al panel como admin.
    owner = TestClient(app)
    owner.post(
        "/registro",
        data={
            "empresa": "Tienda Sur",
            "email": "duena@tiendasur.example",
            "clave": "clave-sur-123",
        },
    )
    token = re.search(r"token=([\w-]+)", box.sent[-1].body)
    assert token is not None
    confirmed = owner.get(f"/registro/confirmar?token={token.group(1)}", follow_redirects=False)
    assert confirmed.status_code == 303
    row = conn.execute("select id from tenants where name = 'Tienda Sur'").fetchone()
    assert row is not None
    tenant = row[0]

    # 2. Sube su política.
    uploaded = owner.post(
        "/panel/onboarding/fuentes",
        data={"csrf": _csrf(owner, "/panel/knowledge")},
        files=[("archivos", ("envios.md", POLICY, "text/markdown"))],
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert "Política de despacho" in owner.get("/panel/knowledge").text

    # 3. Reglas y dominio de su sitio.
    owner.post(
        "/panel/configuracion/reglas",
        data={
            "csrf": _csrf(owner, "/panel/configuracion"),
            "derivar_a": "soporte@tiendasur.example",
        },
    )
    owner.post(
        "/panel/configuracion/widget",
        data={
            "csrf": _csrf(owner, "/panel/configuracion"),
            "nombre": "Tienda Sur",
            "color": "#0f766e",
            "dominios": "www.tiendasur.example",
            "sugerencias": "",
        },
        files=[("logo", ("", b"", "application/octet-stream"))],
    )

    # 4. Prueba en el playground; todavía no atiende en su sitio.
    assert owner.get("/panel/probar").status_code == 200
    slug = re.search(r'data-chat="([^"]+)"', owner.get("/panel/probar").text)
    assert slug is not None
    api = TestClient(app)
    config = api.get("/chat/widget/config", params={"canal": slug.group(1)}, headers=SHOP)
    assert config.json()["allowed"] is False

    # 5. El análisis deja su reporte; pasa el umbral y sale en vivo.
    report = {"resolucion_estimada": 0.8, "adversariales": {"total": 4, "superados": 4}}
    with tenant_scope(conn, tenant):
        conn.execute(
            "insert into onboarding_runs (tenant_id, status, report) values (%s, 'done', %s)",
            (tenant, Jsonb(report)),
        )
    owner.post("/panel/salir-en-vivo", data={"csrf": _csrf(owner, "/panel/test"), "activo": "si"})
    config = api.get("/chat/widget/config", params={"canal": slug.group(1)}, headers=SHOP)
    assert config.json()["allowed"] is True

    # 6. Un cliente pregunta desde su sitio y el asistente responde con su política.
    session = api.post("/chat/session", params={"canal": slug.group(1)}, headers=SHOP)
    assert session.status_code == 201
    answer = api.post(
        "/chat/messages",
        json={"session": session.json()["session"], "text": "¿Cuánto tarda el envío?"},
        headers=SHOP,
    )
    text = " ".join(d["text"] for name, d in _events(answer) if name == "message")
    assert "72 horas" in text, text

    # 7. El caso aparece en su panel y en Métricas.
    with tenant_scope(conn, tenant):
        assert conn.execute("select count(*) from cases").fetchone() == (1,)
    assert owner.get("/panel/inbox").status_code == 200
    assert owner.get("/panel/insights").status_code == 200
