"""Hito 11, tarea 2 — test de aceptación de la revisión de conversaciones (decisión 073).

En cada conversación del panel, el equipo marca una respuesta del asistente como correcta o
incorrecta, con un comentario. Las incorrectas aparecen en "Para mejorar". Con un botón, cada una
se convierte en un caso de eval de la empresa: la pregunta del cliente y la respuesta esperada que
escribe el equipo. Si el motivo es información faltante, se sugiere qué documento cargar.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from simulated.stack import SimulatedStack, build_simulated_stack
from test_panel_ingles import spanish_left

from app import i18n
from app.contacts import hash_address
from app.db.connection import tenant_scope
from app.main import app
from app.onboarding.evalgen import EVAL_CASE_SCHEMA
from app.panel import auth, english
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
QUESTION = "¿Cuánto tarda el envío a Córdoba?"


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    case_id: uuid.UUID
    reply_id: uuid.UUID  # la respuesta del asistente a QUESTION

    @property
    def tenant(self) -> uuid.UUID:
        return self.stack.seed.tenant_id


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    tenant = stack.seed.tenant_id
    with tenant_scope(conn, tenant):
        contact = conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (tenant, hash_address(f"web:revision-{uuid.uuid4().hex}")),
        ).fetchone()
    assert contact is not None
    result = stack.agent.handle_message(tenant, contact[0], QUESTION)
    assert result.case_id is not None
    with tenant_scope(conn, tenant):
        row = conn.execute(
            "select id from case_messages where case_id = %s and role = 'agent'"
            " order by created_at desc limit 1",
            (result.case_id,),
        ).fetchone()
    assert row is not None
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=tenant,
        guardian=lambda c, t: stack.agent._guardian,
    )
    try:
        yield World(conn, stack, result.case_id, row[0])
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def panel() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def csrf(markup: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', markup)
    assert found is not None
    return found.group(1)


def mark(
    client: TestClient, world: World, verdict: str, reason: str = "", comment: str = ""
) -> Any:
    token = csrf(client.get(f"/panel/casos/{world.case_id}").text)
    return client.post(
        f"/panel/casos/{world.case_id}/revisar",
        data={
            "csrf": token,
            "mensaje": str(world.reply_id),
            "veredicto": verdict,
            "motivo": reason,
            "comentario": comment,
        },
        follow_redirects=False,
    )


def audit_kinds(world: World) -> list[str]:
    with tenant_scope(world.conn, world.tenant):
        rows = world.conn.execute(
            "select kind from audit_events where case_id = %s order by seq", (world.case_id,)
        ).fetchall()
    return [r[0] for r in rows]


def review_ids(page: str) -> list[str]:
    return re.findall(r'action="/panel/mejorar/([0-9a-f-]{36})/eval"', page)


def test_cada_respuesta_del_asistente_se_puede_marcar(world: World) -> None:
    client = panel()
    detail = client.get(f"/panel/casos/{world.case_id}").text
    assert f'action="/panel/casos/{world.case_id}/revisar"' in detail
    assert f'name="mensaje" value="{world.reply_id}"' in detail
    for label in ("Correcta", "Incorrecta", "Información faltante", "Comentario"):
        assert label in detail, label


def test_una_correcta_no_va_a_para_mejorar(world: World) -> None:
    client = panel()
    response = mark(client, world, "correcta", comment="Bien")
    assert response.status_code == 303
    assert "reply_reviewed" in audit_kinds(world)
    assert review_ids(client.get("/panel/mejorar").text) == []
    assert "Marcada como correcta" in client.get(f"/panel/casos/{world.case_id}").text


def test_una_incorrecta_se_convierte_en_caso_de_eval(world: World) -> None:
    client = panel()
    mark(client, world, "incorrecta", "falta_informacion", "No sabe los plazos a Córdoba")

    page = client.get("/panel/mejorar").text
    ids = review_ids(page)
    assert len(ids) == 1
    assert QUESTION in page and "No sabe los plazos a Córdoba" in page
    # Información faltante: sugiere qué documento cargar, con el enlace para subirlo.
    assert "Cargá un documento" in page and 'href="/panel/onboarding"' in page

    expected = "El envío a Córdoba tarda de 3 a 5 días hábiles."
    done = client.post(
        f"/panel/mejorar/{ids[0]}/eval",
        data={"csrf": csrf(page), "respuesta": expected, "palabras": "3 a 5 días, hábiles"},
        follow_redirects=False,
    )
    assert done.status_code == 303 and "aviso=eval-creado" in done.headers["location"]

    with tenant_scope(world.conn, world.tenant):
        rows = world.conn.execute(
            "select input, expected, must_escalate, origin, case_type_id from eval_cases"
            " where origin = %s",
            (f"revision:{world.case_id}",),
        ).fetchall()
    assert len(rows) == 1
    case_input, case_expected, must_escalate, origin, _ = rows[0]
    case = {"mensaje": case_input["mensaje"], "contacto": case_input["contacto"]}
    case |= {"esperado": case_expected, "origen": origin}
    Draft202012Validator(EVAL_CASE_SCHEMA).validate(case)
    assert case_input["mensaje"] == QUESTION
    assert case_expected["respuesta_esperada"] == expected
    assert case_expected["palabras_clave"] == ["3 a 5 días", "hábiles"]
    assert case_expected["debe_escalar"] is False and must_escalate is False
    assert case_expected["categoria"] == "revision"

    # Ya convertida: sale de la lista y no se puede convertir dos veces.
    assert review_ids(client.get("/panel/mejorar").text) == []
    client.post(f"/panel/mejorar/{ids[0]}/eval", data={"csrf": csrf(page), "respuesta": "x"})
    with tenant_scope(world.conn, world.tenant):
        count = world.conn.execute(
            "select count(*) from eval_cases where origin = %s", (f"revision:{world.case_id}",)
        ).fetchone()
    assert count is not None and count[0] == 1
    assert audit_kinds(world).count("eval_case_created") == 1


def test_si_debia_derivar_el_caso_espera_una_derivacion(world: World) -> None:
    client = panel()
    mark(client, world, "incorrecta", "debio_derivar", "Tenía que pasarlo a una persona")
    page = client.get("/panel/mejorar").text
    assert "Cargá un documento" not in page
    client.post(
        f"/panel/mejorar/{review_ids(page)[0]}/eval",
        data={"csrf": csrf(page), "respuesta": "Te paso con una persona del equipo."},
    )
    with tenant_scope(world.conn, world.tenant):
        row = world.conn.execute(
            "select expected, must_escalate from eval_cases where origin = %s",
            (f"revision:{world.case_id}",),
        ).fetchone()
    assert row is not None
    assert row[0]["accion"] == "derivar" and row[0]["debe_escalar"] is True and row[1] is True


def test_volver_a_marcar_reemplaza_la_revision(world: World) -> None:
    client = panel()
    mark(client, world, "incorrecta", "tono", "Muy seco")
    mark(client, world, "correcta")
    assert review_ids(client.get("/panel/mejorar").text) == []
    with tenant_scope(world.conn, world.tenant):
        count = world.conn.execute(
            "select count(*) from reply_reviews where message_id = %s", (world.reply_id,)
        ).fetchone()
    assert count is not None and count[0] == 1


def test_sin_respuesta_esperada_no_crea_el_caso(world: World) -> None:
    client = panel()
    mark(client, world, "incorrecta", "informacion_incorrecta")
    page = client.get("/panel/mejorar").text
    client.post(f"/panel/mejorar/{review_ids(page)[0]}/eval", data={"csrf": csrf(page)})
    assert len(review_ids(client.get("/panel/mejorar").text)) == 1


def test_solo_se_marcan_respuestas_del_asistente_del_caso(world: World) -> None:
    client = panel()
    with tenant_scope(world.conn, world.tenant):
        row = world.conn.execute(
            "select id from case_messages where case_id = %s and role = 'customer'",
            (world.case_id,),
        ).fetchone()
    assert row is not None
    token = csrf(client.get(f"/panel/casos/{world.case_id}").text)
    response = client.post(
        f"/panel/casos/{world.case_id}/revisar",
        data={"csrf": token, "mensaje": str(row[0]), "veredicto": "incorrecta"},
    )
    assert response.status_code == 404
    assert "reply_reviewed" not in audit_kinds(world)


def test_para_mejorar_en_ingles(world: World) -> None:
    i18n.set_language(world.conn, world.tenant, "en")
    client = panel()
    mark(client, world, "incorrecta", "falta_informacion", "Missing shipping times")
    page = client.get("/panel/mejorar").text
    assert ">To improve<" in page and "Upload a document" in page
    leftover = [t for t in spanish_left(page) if QUESTION not in t and "Acme Store" not in t]
    assert leftover == []
    detail = client.get(f"/panel/casos/{world.case_id}").text
    assert "Incorrect" in detail and "Missing information" in detail
