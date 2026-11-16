"""Demo en inglés, cambio C — test de aceptación de las fechas relativas.

Las fechas de los pedidos de la API simulada de Northwind (envío, entrega estimada, demoras y
vencimiento de cupones) se calculan respecto de la fecha actual al crear la API: al arrancar y
con cada "Reset demo". El agente nunca dice una fecha que ya pasó, el control de fundamento las
acepta y ningún test depende del día en que se corre.
"""

import json
from datetime import date, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.english import long_date
from simulated.stack import build_simulated_stack

import northwind_api
from app.agent.grounding import check_grounding
from app.demo import northwind

# Un día cualquiera, un cambio de año y un 29 de febrero.
DAYS = [date(2026, 11, 16), date(2026, 12, 30), date(2028, 2, 29)]


def _day(moment: str) -> date:
    return datetime.fromisoformat(moment).date()


@pytest.mark.parametrize("today", DAYS)
def test_los_pedidos_del_guion_quedan_en_fechas_relativas_a_hoy(today: date) -> None:
    api = TestClient(northwind_api.create_app(today=today))
    late = api.get("/orders/NW-4521").json()
    # Enviado hace 5 días, ya pasó la fecha original y la nueva es en 2 días.
    assert _day(late["shipped_at"]) == today - timedelta(days=5)
    assert date.fromisoformat(late["estimated_delivery"]) < today
    assert date.fromisoformat(late["delay"]["new_estimated_delivery"]) == today + timedelta(days=2)
    # El del reembolso se entregó hace pocos días: está dentro de los 30 de la política.
    delivered = _day(api.get("/orders/NW-4528").json()["delivered_at"])
    assert today - timedelta(days=30) < delivered < today
    # Los que no llegaron todavía tienen la entrega en el futuro.
    for order in ("NW-4540", "NW-4545"):
        assert date.fromisoformat(api.get(f"/orders/{order}").json()["estimated_delivery"]) > today
    # El cupón vence a los 60 días de hoy.
    coupon = api.post("/orders/NW-4521/coupons", json={"percent": 10}).json()
    assert date.fromisoformat(coupon["expires_on"]) == today + timedelta(days=60)


def test_todas_las_fechas_se_corren_lo_mismo() -> None:
    """Las distancias entre fechas (y el orden de los pedidos) no cambian con el día."""
    before = TestClient(northwind_api.create_app(today=DAYS[0])).get("/orders/NW-4538").json()
    after = TestClient(northwind_api.create_app(today=DAYS[2])).get("/orders/NW-4538").json()
    shift = DAYS[2] - DAYS[0]
    for field in ("created_at", "shipped_at"):
        assert datetime.fromisoformat(after[field]) - datetime.fromisoformat(before[field]) == shift
    assert (
        date.fromisoformat(after["delay"]["new_estimated_delivery"])
        - date.fromisoformat(before["delay"]["new_estimated_delivery"])
        == shift
    )


def test_sin_fecha_usa_la_de_hoy_y_reset_demo_la_vuelve_a_calcular(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for today in DAYS[:2]:
        monkeypatch.setattr(northwind_api, "store_today", lambda today=today: today)
        northwind.shared_northwind.cache_clear()  # lo que hace "Reset demo"
        late = northwind.shared_northwind().get("/orders/NW-4521").json()
        assert late["delay"]["new_estimated_delivery"] == (today + timedelta(days=2)).isoformat()
    northwind.shared_northwind.cache_clear()


@pytest.mark.parametrize("today", DAYS)
def test_el_control_de_fundamento_acepta_las_fechas_corridas(today: date) -> None:
    order = TestClient(northwind_api.create_app(today=today)).get("/orders/NW-4521").json()
    new_date = date.fromisoformat(order["delay"]["new_estimated_delivery"])
    evidence = [json.dumps(order)]
    said = f"The new estimated delivery date is {long_date(new_date.isoformat())}."
    assert check_grounding(said, [], True, {}, evidence, True, language="en").ok
    wrong = f"It arrives on {long_date((new_date + timedelta(days=1)).isoformat())}."
    assert not check_grounding(wrong, [], True, {}, evidence, True, language="en").ok


def test_el_agente_da_la_fecha_nueva_del_pedido_atrasado(conn: psycopg.Connection) -> None:
    """De punta a punta, en un día que no es el de los fixtures: el agente da la fecha corrida
    y el control de fundamento la acepta (si no, derivaría)."""
    today = date(2027, 3, 10)
    stack = build_simulated_stack(conn, company="northwind", today=today)
    stack.add_contact("web")
    stack.send("web", "Where is my order NW-4521?")
    stack.send("web", "My email is emily@example.com")
    answer = stack.send("web", stack.last_code())
    assert answer.action == "responder", answer.message
    assert "March 12" in answer.message and "November" not in answer.message


def test_los_tests_y_evals_usan_el_dia_de_los_fixtures(conn: psycopg.Connection) -> None:
    """Los casos guionados de los evals esperan las fechas de `data/orders.json` tal cual."""
    stack = build_simulated_stack(conn, company="northwind")
    late = stack.acme_order("NW-4521")
    assert late["delay"]["new_estimated_delivery"] == "2026-11-14"
    assert northwind_api.DATES_AS_OF == date(2026, 11, 12)
