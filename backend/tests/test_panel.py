"""Panel mínimo de la empresa (Hito 5, demo): login, aprobaciones, bandeja, detalle de caso y
reporte de preparación. Páginas simples servidas por la misma API (decisión 022)."""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from simulated.stack import SimulatedStack, build_simulated_stack

from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
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
def client(
    conn: psycopg.Connection, stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("PANEL_EMAIL", EMAIL)
    monkeypatch.setenv("PANEL_PASSWORD", PASSWORD)
    address = f"web:test-{uuid.uuid4().hex}"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, tenant: Guardian(c, ToolRuntime(stack.acme)),
        report_tenant=stack.seed.tenant_id,
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent, address=address
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def login(client: TestClient) -> None:
    response = client.post(
        "/panel/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_el_panel_pide_login_y_rechaza_una_clave_incorrecta(client: TestClient) -> None:
    for path in ("/panel/casos", "/panel/aprobaciones", "/panel/reporte"):
        assert client.get(path, follow_redirects=False).headers["location"] == "/panel/login"
    bad = client.post("/panel/login", data={"email": EMAIL, "password": "otra"})
    assert bad.status_code == 401
    login(client)
    assert client.get("/panel/casos").status_code == 200


def test_aprobar_un_reembolso_lo_ejecuta_y_el_cliente_lo_ve(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    pending = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert pending.action == "pedir_aprobacion" and pending.approval_id is not None
    login(client)
    page = client.get("/panel/aprobaciones").text
    # Textos legibles, sin identificadores de la herramienta ni de sus parámetros.
    assert "Reembolso · Pedido 1005 · $35.000" in page
    assert "iniciar_reembolso" not in page and "order_id" not in page

    forged = client.post(
        f"/panel/aprobaciones/{pending.approval_id}", data={"decision": "aprobar", "csrf": "x"}
    )
    assert forged.status_code == 403  # sin el token del formulario no se decide nada

    done = client.post(
        f"/panel/aprobaciones/{pending.approval_id}",
        data={"decision": "aprobar", "csrf": csrf(page)},
        follow_redirects=False,
    )
    assert done.status_code == 303
    with tenant_scope(conn, stack.seed.tenant_id):
        status = conn.execute(
            "select status, decided_by from approvals where id = %s", (pending.approval_id,)
        ).fetchone()
        told = conn.execute(
            "select text from case_messages where case_id = %s and role = 'human'",
            (pending.case_id,),
        ).fetchall()
    assert status is not None and status[0] == "approved" and status[1] is not None
    assert len(told) == 1 and "aprob" in told[0][0].lower() and "1005" in told[0][0]
    after = client.get("/panel/aprobaciones").text  # pasa de pendiente al historial
    assert "No hay pedidos esperando aprobación" in after and "Aprobado" in after


def test_la_bandeja_muestra_primero_las_derivadas_y_se_responde_como_humano(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    stack.send("lucia", "¿Abren los domingos?")
    stack.close_open_cases()
    escalated = stack.send("martin", "quiero hablar con una persona")
    login(client)
    inbox = client.get("/panel/casos").text
    assert inbox.index(str(escalated.case_id)) < inbox.index("Abren los domingos")

    detail = client.get(f"/panel/casos/{escalated.case_id}").text
    assert "Derivado" in detail and "pidió hablar con una persona" in detail
    reply = client.post(
        f"/panel/casos/{escalated.case_id}/responder",
        data={"texto": "Hola Martín, soy Ana del equipo. ¿En qué te ayudo?", "csrf": csrf(detail)},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    with tenant_scope(conn, stack.seed.tenant_id):
        kinds = [
            r[0]
            for r in conn.execute(
                "select kind from audit_events where case_id = %s order by seq",
                (escalated.case_id,),
            ).fetchall()
        ]
    assert kinds[-1] == "human_reply"


def field(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_enviar_dos_veces_el_mismo_formulario_manda_un_solo_mensaje(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    """Doble clic o reenvío del navegador: el cliente recibe la respuesta una sola vez."""
    pending = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert pending.approval_id is not None
    stack.close_open_cases()  # la derivación, en otro caso
    escalated = stack.send("martin", "quiero hablar con una persona")
    login(client)
    detail = client.get(f"/panel/casos/{escalated.case_id}").text
    assert "dataset.sent" in detail  # el navegador no reenvía un formulario ya enviado
    form = {"texto": "Hola, soy Ana.", "csrf": csrf(detail), "envio": field(detail, "envio")}
    for _ in range(2):
        response = client.post(
            f"/panel/casos/{escalated.case_id}/responder", data=form, follow_redirects=False
        )
        assert response.status_code == 303

    approvals = client.get("/panel/aprobaciones").text
    for _ in range(2):  # la segunda vez ya está decidida: vuelve a la lista sin error
        again = client.post(
            f"/panel/aprobaciones/{pending.approval_id}",
            data={"decision": "aprobar", "csrf": csrf(approvals)},
            follow_redirects=False,
        )
        assert again.status_code == 303
    with tenant_scope(conn, stack.seed.tenant_id):
        counts = conn.execute(
            "select case_id, count(*) from case_messages where role = 'human' group by case_id"
        ).fetchall()
    assert all(count == 1 for _, count in counts) and counts


def test_los_tipos_de_caso_se_ven_con_nombre_legible(
    client: TestClient, stack: SimulatedStack
) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    login(client)
    inbox = client.get("/panel/casos").text
    assert "Horarios y sucursales" in inbox and ">horarios_y_sucursales<" not in inbox
    detail = client.get(f"/panel/casos/{answered.case_id}").text
    assert "Caso Horarios y sucursales" in detail


def test_la_charla_figura_como_conversacion_general(
    client: TestClient, stack: SimulatedStack
) -> None:
    chat = stack.send("lucia", "¿sos un bot?")  # charla para el clasificador simulado
    login(client)
    inbox = client.get("/panel/casos").text
    assert "Conversación general" in inbox and "Sin clasificar" not in inbox
    assert "Caso Conversación general" in client.get(f"/panel/casos/{chat.case_id}").text


def test_el_resumen_del_caso_no_muestra_codigos_ni_datos_personales(
    client: TestClient, stack: SimulatedStack
) -> None:
    stack.add_contact("nuevo")
    stack.send("nuevo", "Quiero el reembolso del pedido 1005")
    stack.send("nuevo", "martin@example.com")
    code = stack.last_code()
    done = stack.send("nuevo", code)
    assert done.approval_id is not None
    login(client)
    inbox = client.get("/panel/casos").text
    approvals = client.get("/panel/aprobaciones").text
    for page_text in (inbox, approvals):
        assert "Quiero el reembolso del pedido 1005" in page_text  # el primer mensaje
        assert code not in page_text and "martin@example.com" not in page_text
    assert code not in client.get(f"/panel/casos/{done.case_id}").text  # ni en el detalle


def test_la_bandeja_cierra_los_casos_sin_respuesta_hace_24_horas(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    with tenant_scope(conn, stack.seed.tenant_id):
        conn.execute(
            "update case_messages set created_at = created_at - interval '25 hours'"
            " where case_id = %s",
            (answered.case_id,),
        )
    login(client)
    client.get("/panel/casos")
    with tenant_scope(conn, stack.seed.tenant_id):
        row = conn.execute(
            "select status, resolved_by from cases where id = %s", (answered.case_id,)
        ).fetchone()
    assert row == ("resolved", "agent")
    detail = client.get(f"/panel/casos/{answered.case_id}").text
    assert "Resuelto por el asistente" in detail


def test_el_detalle_muestra_la_auditoria_con_fuentes_y_costo(
    client: TestClient, stack: SimulatedStack
) -> None:
    answered = stack.send("lucia", "¿Abren los domingos?")
    login(client)
    page = client.get(f"/panel/casos/{answered.case_id}").text
    for text in (
        "Mensaje del cliente",
        "Clasificó",
        "Buscó en los documentos",
        "horarios_y_sucursales",
        "Costo",
        "USD",
        "Cadena de auditoría íntegra",
    ):
        assert text in page, text


def test_el_cliente_del_chat_web_ve_los_mensajes_del_equipo(client: TestClient) -> None:
    session = client.post("/chat/session").json()["session"]
    client.post(
        "/chat/messages", json={"session": session, "text": "quiero hablar con una persona"}
    )
    assert client.get("/chat/updates", params={"session": session}).json() == {"messages": []}
    login(client)
    inbox = client.get("/panel/casos").text
    case_id = re.findall(r'href="/panel/casos/([0-9a-f-]{36})"', inbox)[0]
    detail = client.get(f"/panel/casos/{case_id}").text
    client.post(
        f"/panel/casos/{case_id}/responder",
        data={"texto": "Hola, soy Ana del equipo.", "csrf": csrf(detail)},
    )
    updates = client.get("/chat/updates", params={"session": session}).json()["messages"]
    assert [m["text"] for m in updates] == ["Hola, soy Ana del equipo."]
    later = client.get("/chat/updates", params={"session": session, "after": updates[0]["at"]})
    assert later.json() == {"messages": []}


def test_el_reporte_de_preparacion_muestra_tipos_volumen_y_porcentaje(
    client: TestClient, conn: psycopg.Connection, stack: SimulatedStack
) -> None:
    report: dict[str, Any] = {
        "conversaciones": 100,
        "sin_tipo": 0,
        "resolucion_estimada": 0.72,
        "resolucion_en_tipos_detectados": 0.72,
        "tipos": [
            {
                "nombre": "consulta_estado_pedido",
                "objetivo": "Informar el estado",
                "volumen": 20,
                "herramientas": ["consultar_pedido"],
                "evals": 2,
                "resueltos": 2,
                "porcentaje": 1.0,
                "errores": [],
                "falta": [],
            },
            {
                "nombre": "obtener_factura",
                "objetivo": "Reenviar la factura",
                "volumen": 3,
                "herramientas": [],
                "evals": 2,
                "resueltos": 1,
                "porcentaje": 0.5,
                "errores": [],
                "falta": ["Una herramienta para actuar en el sistema (hoy solo puede informar)"],
            },
        ],
        "adversariales": {"total": 4, "superados": 4, "fallas": []},
    }
    with tenant_scope(conn, stack.seed.tenant_id):
        conn.execute(
            "insert into onboarding_runs (tenant_id, status, report) values (%s, 'done', %s)",
            (stack.seed.tenant_id, Jsonb(report)),
        )
    login(client)
    page = client.get("/panel/reporte").text
    for text in (
        "72%",
        "Consulta estado pedido",
        "100%",
        "Obtener factura",
        "50%",
        "Una herramienta para actuar",
        "4 de 4",
    ):
        assert text in page, text
