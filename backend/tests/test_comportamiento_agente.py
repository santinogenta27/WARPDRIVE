"""Hito 8, tarea 5 — test de aceptación del comportamiento del agente (decisión 040).

- Empatía antes de resolver si el cliente está molesto: un control en código lo exige.
- Opciones cuando hay más de una salida válida (pedido atrasado: cupón o envío express).
- Cambio de idea a mitad de la conversación: lo pendiente se cancela antes de hacer lo nuevo, y
  eso lo aplica el guardián.
- Dos temas en un mismo mensaje: el clasificador marca el segundo y el agente atiende los dos.
- El clasificador sigue el tema de la conversación (una respuesta corta, elegir una opción).
- Nunca afirma una acción que no hizo una herramienta en el turno.

Cada comportamiento tiene su caso de evals en inglés, incluido el guion completo de la demo.
"""

import json
import uuid
from typing import Any

import psycopg
import pytest
import runner
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import conduct
from app.agent.classifier import classify
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.demo import northwind
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.llm.types import LLMResponse, TokenUsage, ToolCall
from app.policy.guardian import CANCEL_PENDING, ContactContext, Guardian
from app.tools.runtime import ToolRuntime

BEHAVIOR_CASES = {
    "nw17_upset_customer_gets_empathy_first",
    "nw18_options_for_a_late_order",
    "nw19_change_of_mind_cancels_the_pending_refund",
    "nw20_two_topics_in_one_message",
    "nw21_demo_script",
}


# -- 1. el clasificador: tema de la conversación y segundo tema --------------------------------
class _Recorder:
    """LLMClient mínimo: guarda el pedido y devuelve la clasificación indicada."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def complete(self, **request: Any) -> LLMResponse:
        self.calls.append(request)
        call = ToolCall("t1", "clasificar", self.answer)
        return LLMResponse("", "sim", "tool_use", TokenUsage(1, 1), tool_calls=[call])


def test_el_clasificador_ve_el_tema_actual_y_puede_marcar_un_segundo_tema() -> None:
    llm = _Recorder({"tipo": "exchange", "otro_tipo": "where_is_my_order"})
    found = classify(
        llm,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        case_id=None,
        company="Northwind Outfitters",
        message="Where is NW-4521? Also, exchange my sweater from NW-4533 for an L",
        case_types={"exchange": "Exchange an item", "where_is_my_order": "Order status"},
        current="where_is_my_order",
        language="en",
    )
    request = llm.calls[0]
    assert "<tipo_actual>where_is_my_order</tipo_actual>" in request["messages"][0]["content"]
    schema = request["tools"][0]["input_schema"]["properties"]
    assert set(schema["otro_tipo"]["enum"]) == {"exchange", "where_is_my_order"}
    assert (found.label, found.also) == ("exchange", "where_is_my_order")


def test_un_segundo_tema_igual_al_primero_o_desconocido_no_cuenta() -> None:
    for answer in ({"tipo": "exchange", "otro_tipo": "exchange"}, {"tipo": "exchange"}):
        found = classify(
            _Recorder(answer),  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
            case_id=None,
            company="Acme",
            message="hola",
            case_types={"exchange": "Exchange"},
            language="en",
        )
        assert found.also is None


# -- 2. no afirmar acciones que no hizo, y empatía si el cliente está molesto -----------------
@pytest.mark.parametrize(
    ("text", "language", "claims"),
    [
        ("Got it! I've added free express shipping to your next order.", "en", True),
        ("Your free express shipping has been activated.", "en", True),
        ("Done! I started a refund of $56.", "en", True),
        ("I can add free express shipping to your next order. Would you like that?", "en", False),
        ("The express shipping you chose earlier is already on your account.", "en", False),
        ("Listo, ya te agregué el envío express a tu próxima compra.", "es", True),
        ("Puedo iniciar el reembolso si querés.", "es", False),
    ],
)
def test_afirmar_una_accion(text: str, language: str, claims: bool) -> None:
    assert conduct.claims_action(text, language) is claims


@pytest.mark.parametrize(
    ("message", "upset"),
    [
        ("This is ridiculous, my order still hasn't arrived!!", True),
        ("Honestly, that's frustrating.", True),
        ("Where is my order NW-4521?", False),
    ],
)
def test_cliente_molesto(message: str, upset: bool) -> None:
    assert conduct.is_upset(message, "en") is upset


def test_los_numeros_de_una_lista_de_opciones_no_son_datos() -> None:
    from app.agent.grounding import check_grounding

    message = "You can choose:\n\n1. A 10% coupon.\n2. Free express shipping.\n3) A refund."
    docs = {"discounts_and_coupons": "Courtesy coupons of up to 10% off the next order."}
    verdict = check_grounding(
        message, ["discounts_and_coupons"], True, docs, [], False, language="en"
    )
    assert verdict.ok, verdict.reason
    # Un número inventado dentro de la lista se sigue rechazando.
    invented = "You can choose:\n1. A 25% coupon.\n2. Free express shipping."
    assert not check_grounding(invented, ["discounts_and_coupons"], True, docs, [], False).ok


def test_la_empatia_se_reconoce() -> None:
    assert conduct.shows_empathy("I'm so sorry for the wait. Your order...", "en")
    assert conduct.shows_empathy("I understand how frustrating this is.", "en")
    assert not conduct.shows_empathy("Your order NW-4521 is on its way.", "en")


def _events(stack: SimulatedStack, case_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    from app.audit.log import reconstruct_case

    return [
        (e.kind, e.payload) for e in reconstruct_case(stack.conn, stack.seed.tenant_id, case_id)
    ]


def test_una_accion_afirmada_sin_herramienta_se_rechaza(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="afirma_sin_hacer", company="northwind")
    result = stack.send("emily", "What does express shipping cost?")
    rejected = [p for k, p in _events(stack, result.case_id) if k == "grounding_rejected"]
    assert rejected and "acción" in rejected[0]["reason"]
    assert not conduct.claims_action(result.message, "en"), result.message


def test_con_el_cliente_molesto_la_respuesta_empieza_con_empatia(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="sin_empatia", company="northwind")
    result = stack.send("emily", "This is ridiculous, where is my order NW-4521?!")
    kinds = [k for k, _ in _events(stack, result.case_id)]
    assert "tone_rejected" in kinds
    assert conduct.shows_empathy(result.message, "en"), result.message


# -- 3. cambio de idea: el guardián exige cancelar lo pendiente ---------------------------------
@pytest.fixture
def store(conn: psycopg.Connection) -> tuple[Guardian, Any, uuid.UUID, ContactContext]:
    import httpx
    from simulated.voyage import voyage_transport

    embedder = VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )
    seed = northwind.seed_northwind(conn, embedder)
    guardian = Guardian(conn, ToolRuntime(northwind.sandbox_client()))
    with tenant_scope(conn, seed.tenant_id):
        row = conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (seed.tenant_id, seed.contacts["emily"]),
        ).fetchone()
    assert row is not None
    emily = ContactContext(seed.contacts["emily"], hash_phone("+15555550101"), True)
    return guardian, seed, row[0], emily


def _run(store: Any, tool: str, params: dict[str, Any]) -> Any:
    guardian, seed, case_id, emily = store
    return guardian.execute(
        tenant_id=seed.tenant_id,
        case_id=case_id,
        contact=emily,
        tool_name=tool,
        tool_input=params,
        allowed={"iniciar_reembolso", "iniciar_cambio", "consultar_pedido"},
    )


EXCHANGE = {"order_id": "NW-4528", "item_sku": "NW-CHB-BRN-8", "new_size": "9"}


def test_con_algo_pendiente_lo_nuevo_sobre_el_mismo_pedido_espera_la_cancelacion(
    store: Any,
) -> None:
    assert _run(store, "iniciar_reembolso", {"order_id": "NW-4528", "amount": 213}).status == (
        "approval_required"
    )
    blocked = _run(store, "iniciar_cambio", EXCHANGE)
    assert blocked.status == "denied"
    assert blocked.result["pendiente"]["herramienta"] == "iniciar_reembolso"
    # Otro monto de la misma herramienta también es un cambio de idea.
    other = _run(store, "iniciar_reembolso", {"order_id": "NW-4528", "amount": 24})
    assert other.status == "denied" and "pendiente" in other.result
    # Lo mismo otra vez no: se reusa la aprobación pendiente (sin duplicarla).
    again = _run(store, "iniciar_reembolso", {"order_id": "NW-4528", "amount": 213})
    assert again.status == "approval_required"


def test_cancelar_lo_pendiente_y_hacer_lo_nuevo(store: Any) -> None:
    guardian, seed, case_id, _ = store
    _run(store, "iniciar_reembolso", {"order_id": "NW-4528", "amount": 213})
    cancelled = guardian.cancel_pending(seed.tenant_id, case_id, {"order_id": "NW-4528"})
    assert cancelled.status == "executed"
    assert cancelled.result["canceladas"][0]["herramienta"] == "iniciar_reembolso"
    with tenant_scope(guardian._conn, seed.tenant_id):
        status = guardian._conn.execute(
            "select a.status, c.status from approvals a join cases c on c.id = a.case_id"
            " where a.case_id = %s",
            (case_id,),
        ).fetchone()
    assert status == ("rejected", "open")  # nadie la ejecuta después
    assert _run(store, "iniciar_cambio", EXCHANGE).status == "executed"
    # Sin nada pendiente, cancelar no hace nada.
    empty = guardian.cancel_pending(seed.tenant_id, case_id, {})
    assert empty.status == "executed" and empty.result["canceladas"] == []


def test_cancelar_pendiente_se_ofrece_solo_con_algo_pendiente() -> None:
    from app.agent import prompt

    tools = prompt.tool_definitions([], english=True, pending=True)
    assert CANCEL_PENDING in {t["name"] for t in tools}
    assert CANCEL_PENDING not in {t["name"] for t in prompt.tool_definitions([], english=True)}


# -- 4. los casos de evals en inglés de cada comportamiento -----------------------------------
def test_los_casos_de_comportamiento_se_resuelven_con_el_modelo_simulado(
    conn: psycopg.Connection,
) -> None:
    cases = json.loads(runner.cases_path("northwind").read_text(encoding="utf-8"))
    chosen = [c for c in cases if c["id"] in BEHAVIOR_CASES]
    assert {c["id"] for c in chosen} == BEHAVIOR_CASES
    stack = build_simulated_stack(conn, company="northwind")
    high_risk = runner._high_risk_tools(stack)
    failed = {}
    for case in chosen:
        stack.reset_sandbox()
        graded = runner.grade(case, runner.send_case(stack, case), high_risk)
        if not graded.resolved:
            failed[case["id"]] = (graded.checks, graded.critical, graded.message)
        stack.close_open_cases()
    assert failed == {}
