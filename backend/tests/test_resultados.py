"""Hito 12, tarea 6 — test de aceptación de los resultados medibles (decisión 083).

- "Resolución autónoma" auditable: sin derivación, sin reapertura en 72 h y sin CSAT negativo.
  Informe mensual por empresa, exportable.
- Análisis de conversaciones: temas, motivos de derivación y preguntas sin respuesta, agrupados,
  con ejemplos sin datos personales.
- Informe semanal por email a los admins, idempotente y desactivable.
"""

import csv
import io
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import RecordingMailer, SimulatedStack, build_simulated_stack

from app.audit.log import reconstruct_case
from app.cases import billing, closing
from app.cases.billing import ARGENTINA, current_time
from app.contacts import hash_address
from app.db.connection import tenant_scope
from app.main import app
from app.outcomes import analysis, autonomy, weekly
from app.panel import auth, english
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"


class World:
    def __init__(self, stack: SimulatedStack) -> None:
        self.stack = stack
        self.conn = stack.conn
        self.tenant = stack.seed.tenant_id

    def case(
        self,
        *,
        hours_ago: float = 100,
        by: str = "agent",
        escalated: bool = False,
        csat: bool | None = None,
        first: str = "¿Abren los domingos?",
        kind: str | None = "horarios_y_sucursales",
    ) -> uuid.UUID:
        """Un caso resuelto hace `hours_ago` horas."""
        closed = current_time() - timedelta(hours=hours_ago)
        with tenant_scope(self.conn, self.tenant):
            contact = self.conn.execute(
                "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
                (self.tenant, hash_address(f"web:resultados-{uuid.uuid4().hex}")),
            ).fetchone()
            assert contact is not None
            row = self.conn.execute(
                "insert into cases (tenant_id, contact_id, case_type_id, status, resolved_by,"
                " escalation, created_at, closed_at) values (%s, %s,"
                " (select id from case_types where name = %s), 'resolved', %s, %s, %s, %s)"
                " returning id",
                (
                    self.tenant,
                    contact[0],
                    kind,
                    by,
                    json.dumps({"motivo": "x"}) if escalated else None,
                    closed - timedelta(minutes=5),
                    closed,
                ),
            ).fetchone()
            assert row is not None
            self.conn.execute(
                "insert into case_messages (tenant_id, case_id, role, text) values (%s, %s,"
                " 'customer', %s)",
                (self.tenant, row[0], first),
            )
            if csat is not None:
                self.conn.execute(
                    "insert into csat_responses (tenant_id, case_id, resolved, answered_at)"
                    " values (%s, %s, %s, now())",
                    (self.tenant, row[0], csat),
                )
        case_id: uuid.UUID = row[0]
        return case_id


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
    )
    try:
        yield World(stack)
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def this_month(world: World) -> autonomy.Report:
    now = current_time()
    start = now - timedelta(days=40)
    return autonomy.between(world.conn, world.tenant, start, now + timedelta(minutes=1))


# -- resolución autónoma ---------------------------------------------------------------------------
def test_cada_caso_tiene_su_estado_auditable(world: World) -> None:
    autonomous = world.case()
    in_window = world.case(hours_ago=10)
    handed_off = world.case(escalated=True, by="human")
    by_person = world.case(by="human")
    unhappy = world.case(csat=False)
    happy = world.case(csat=True)
    states = {c.case_id: c.state for c in this_month(world).cases}
    assert states[autonomous] == "autonoma"
    assert states[in_window] == "en_ventana"
    assert states[handed_off] == "derivada"
    assert states[by_person] == "con_persona"
    assert states[unhappy] == "csat_negativo"
    assert states[happy] == "autonoma"


def test_una_reapertura_en_72_h_quita_la_resolucion_autonoma(world: World) -> None:
    early = world.case(hours_ago=5)
    late = world.case(hours_ago=100)
    for case_id in (early, late):
        billing.reopen(world.conn, world.tenant, case_id)
        closing.resolve(world.conn, world.tenant, case_id, "El cliente confirmó.")
        with tenant_scope(world.conn, world.tenant):  # se cerró de nuevo hace 4 días
            world.conn.execute(
                "update cases set closed_at = now() - interval '4 days', resolved_by = 'agent'"
                " where id = %s",
                (case_id,),
            )
    reopened = [
        e.payload
        for e in reconstruct_case(world.conn, world.tenant, early)
        if e.kind == "case_reopened"
    ]
    assert reopened[0]["horas_desde_cierre"] == pytest.approx(5, abs=0.2)
    states = {c.case_id: c.state for c in this_month(world).cases}
    assert states[early] == "reabierta_72h"
    assert states[late] == "autonoma"  # se reabrió pasadas las 72 h


def test_la_tasa_no_cuenta_los_casos_en_ventana(world: World) -> None:
    world.case()
    world.case(escalated=True)
    world.case(hours_ago=1)
    report = this_month(world)
    assert report.total == 3 and report.autonomous == 1
    assert report.rate == 0.5


def test_el_informe_del_mes_se_exporta_sin_datos_del_cliente(world: World) -> None:
    case_id = world.case(first="Soy Lucía, mi email es lucia@example.com")
    client = TestClient(app)
    client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD})
    month = current_time().strftime("%Y-%m")
    page = client.get(f"/panel/insights/results?mes={month}")
    assert page.status_code == 200 and "Resolución autónoma" in page.text
    response = client.get(f"/panel/resultados/export?mes={month}")
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    mine = [r for r in rows if r["caso"] == str(case_id)]
    if current_time().day > 4:  # el caso de hace 100 horas cayó en este mes
        assert mine and mine[0]["estado"] == "autonoma" and mine[0]["derivado"] == "no"
    assert "lucia" not in response.text.lower()


# -- análisis ------------------------------------------------------------------------------------
def test_el_analisis_agrupa_temas_derivaciones_y_preguntas_sin_respuesta(world: World) -> None:
    stack = world.stack
    stack.send("lucia", "¿Hacen arreglos de ruedo y costura?")  # sin documentos: deriva
    stack.close_open_cases()
    stack.send("martin", "¿Hacen arreglos de ruedo en el pantalón?")
    stack.close_open_cases()
    stack.send("lucia", "Quiero hablar con una persona")
    stack.close_open_cases()
    stack.send("lucia", "¿Abren los domingos?")
    found = analysis.analyze(world.conn, world.tenant)
    assert {t["tipo"] for t in found["temas"]} >= {"horarios_y_sucursales"}
    causes = {d["causa"]: d["casos"] for d in found["derivaciones"]}
    assert causes.get("pidio_persona") == 1
    unanswered = found["sin_respuesta"]
    assert unanswered["total"] >= 2
    [group] = [g for g in unanswered["grupos"] if g["tema"] == "arreglos"]
    assert group["cantidad"] == 2 and len(group["ejemplos"]) == 2


def test_los_ejemplos_no_tienen_datos_personales() -> None:
    text = (
        "Soy Lucía Ferrari, mi mail es lucia@example.com y mi cel +54 9 11 5555-0101, pedido 1004"
    )
    cleaned = analysis.redact(text, ["Lucía Ferrari"])
    assert "Lucía" not in cleaned and "Ferrari" not in cleaned
    assert "lucia@example.com" not in cleaned and "5555" not in cleaned and "1004" not in cleaned


def test_agrupar_preguntas_por_la_palabra_que_comparten() -> None:
    groups = analysis.group_questions(
        [
            "¿Hacen arreglos de ruedo?",
            "¿Cuánto salen los arreglos?",
            "¿Venden gift cards?",
            "¿Tienen gift cards de regalo?",
            "¿Abren en Mendoza?",
        ]
    )
    topics = {g["tema"]: g["cantidad"] for g in groups}
    assert topics["arreglos"] == 2 and topics["cards"] == 2 and topics[""] == 1


# -- informe semanal -------------------------------------------------------------------------
def make_company(world: World, *, owners: int = 1) -> uuid.UUID:
    """Una empresa común (no de demo), con admins."""
    row = world.conn.execute(
        "insert into tenants (name, settings) values (%s, '{}') returning id",
        (f"Tienda {uuid.uuid4().hex[:6]}",),
    ).fetchone()
    assert row is not None
    tenant: uuid.UUID = row[0]
    with tenant_scope(world.conn, tenant):
        for n in range(owners):
            world.conn.execute(
                "insert into users (tenant_id, email, role) values (%s, %s, 'owner')",
                (tenant, f"admin{n}-{uuid.uuid4().hex[:6]}@tienda.example"),
            )
    return tenant


def monday_noon() -> datetime:
    now = current_time().astimezone(ARGENTINA)
    return (now - timedelta(days=now.weekday())).replace(hour=12, minute=0)


def sent_to(mailer: RecordingMailer, tenant_name: str) -> list[Any]:
    return [m for m in mailer.sent if m.subject.startswith(tenant_name)]


def test_el_informe_semanal_sale_una_vez_a_los_admins(world: World) -> None:
    tenant = make_company(world, owners=2)
    name = world.conn.execute("select name from tenants where id = %s", (tenant,)).fetchone()[0]  # type: ignore[index]
    mailer = RecordingMailer()
    weekly.send_due(world.conn, mailer, now=monday_noon())
    mine = sent_to(mailer, name)
    assert len(mine) == 2
    assert "Conversaciones:" in mine[0].body and "resoluciones autónomas" in mine[0].body
    weekly.send_due(world.conn, mailer, now=monday_noon())  # otra vuelta del worker
    assert len(sent_to(mailer, name)) == 2  # idempotente


def test_si_el_envio_falla_se_reintenta(world: World) -> None:
    tenant = make_company(world)
    name = world.conn.execute("select name from tenants where id = %s", (tenant,)).fetchone()[0]  # type: ignore[index]
    weekly.send_due(world.conn, RecordingMailer(failing=True), now=monday_noon())
    mailer = RecordingMailer()
    weekly.send_due(world.conn, mailer, now=monday_noon())
    assert len(sent_to(mailer, name)) == 1


def test_el_informe_semanal_se_desactiva(world: World) -> None:
    tenant = make_company(world)
    name = world.conn.execute("select name from tenants where id = %s", (tenant,)).fetchone()[0]  # type: ignore[index]
    weekly.set_enabled(world.conn, tenant, False)
    mailer = RecordingMailer()
    weekly.send_due(world.conn, mailer, now=monday_noon())
    assert sent_to(mailer, name) == []


def test_la_empresa_de_demo_no_lo_recibe(world: World) -> None:
    world.conn.execute(
        'update tenants set settings = settings || \'{"demo": "acme"}\' where id = %s',
        (world.tenant,),
    )
    with tenant_scope(world.conn, world.tenant):
        world.conn.execute(
            "insert into users (tenant_id, email, role) values (%s, 'dueno@acme.example', 'owner')",
            (world.tenant,),
        )
    mailer = RecordingMailer()
    weekly.send_due(world.conn, mailer, now=monday_noon())
    assert not [m for m in mailer.sent if m.to == "dueno@acme.example"]


def test_el_panel_desactiva_el_informe_semanal(world: World) -> None:
    client = TestClient(app)
    client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD})
    page = client.get("/panel/insights/results").text
    assert "Informe semanal" in page and "Temas principales" in page
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    client.post("/panel/resultados/informe-semanal", data={"csrf": token.group(1), "activo": "no"})
    assert weekly.enabled(world.conn, world.tenant) is False
