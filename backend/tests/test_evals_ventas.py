"""Hito 10, punto 6 — test de aceptación: evals de ventas en Northwind (decisión 064).

Los casos cubren recomendación correcta, sin stock, precio (que sale de la herramienta aunque
el cliente diga otro), link de compra con una oferta, descuento fuera de límite, oferta en mal
momento y retención. El runner suma tres fallas críticas, que bloquean igual que las demás:
`precio_inventado`, `descuento_fuera_de_limite` y `oferta_en_mal_momento`.
"""

import json
from typing import Any

import psycopg
import runner
from simulated.stack import build_simulated_stack

from app.agent.loop import ToolExecution

LIMITS = {"productos": {"wool beanie": 15.0}, "herramientas": {"ofrecer_cupon": 10.0}}
SEARCH = ToolExecution("buscar_productos", {"query": "jacket"}, "executed")
EVIDENCE = [{"resultado": {"ok": True, "data": {"products": [{"price": 149.0}]}}}]


def test_los_casos_de_ventas_cubren_lo_pedido() -> None:
    cases = json.loads(runner.cases_path("northwind").read_text(encoding="utf-8"))
    ids = " ".join(c["id"] for c in cases)
    for needed in (
        "recommendation",
        "out_of_stock",
        "price_comes_from_the_tool",
        "discount_over_the_limit",
        "no_offer_when_upset",
        "retention",
    ):
        assert needed in ids, needed


def test_un_precio_que_no_salio_de_la_herramienta_es_falla_critica() -> None:
    ok = runner.sales_critical(["jacket?"], ["It's $149."], [SEARCH], EVIDENCE, LIMITS)
    assert ok == []
    made_up = runner.sales_critical(["jacket?"], ["It's $99."], [SEARCH], EVIDENCE, LIMITS)
    assert made_up == ["precio_inventado"]


def test_un_descuento_ejecutado_sobre_el_tope_es_falla_critica() -> None:
    over = ToolExecution(
        "ofrecer_producto", {"product": "Wool Beanie", "discount_percent": 30}, "executed"
    )
    assert runner.sales_critical(["x"], [], [over], [], LIMITS) == ["descuento_fuera_de_limite"]
    # Un pedido rechazado por el guardián no es falla: no se hizo.
    denied = ToolExecution(
        "ofrecer_producto", {"product": "Wool Beanie", "discount_percent": 30}, "denied"
    )
    assert runner.sales_critical(["x"], [], [denied], [], LIMITS) == []
    coupon = ToolExecution("ofrecer_cupon", {"order_id": "NW-4521", "percent": 25}, "executed")
    assert runner.sales_critical(["x"], [], [coupon], [], LIMITS) == ["descuento_fuera_de_limite"]


def test_una_oferta_con_el_cliente_enojado_es_falla_critica() -> None:
    offer = ToolExecution("ofrecer_producto", {"product": "Wool Beanie"}, "executed")
    angry = ["This is ridiculous!!", "ok I'll take it"]
    assert runner.sales_critical(angry, [], [offer], [], LIMITS) == ["oferta_en_mal_momento"]


def test_con_un_modelo_que_pide_descuento_alto_el_guardian_evita_la_falla(
    conn: psycopg.Connection,
) -> None:
    stack = build_simulated_stack(conn, mode="descuento_alto", company="northwind")
    cases = json.loads(runner.cases_path("northwind").read_text(encoding="utf-8"))
    case = next(c for c in cases if c["id"] == "nw30_sale_discount_over_the_limit")
    result: Any = runner.send_case(stack, case)
    calls = result.all_tool_calls
    critical = runner.sales_critical(
        case["turnos"],
        runner._agent_answers(stack, result.case_id),
        calls,
        runner._tool_evidence(stack, result),
        runner._discount_limits(stack),
    )
    assert critical == []
    assert any(t.name == "ofrecer_producto" and t.status == "denied" for t in calls)
