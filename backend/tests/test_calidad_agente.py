"""Arreglos que salieron de la prueba de modelos del agente (docs/calidad.md, 12/11/2026).

1. El control de fundamento rechazaba la hora sin cero adelante ("9:30" con "09:30").
2. "verificarlo" contaba como pedirle la verificación a un cliente ya verificado.
3. Un cambio de dirección de un pedido despachado derivaba en lugar de explicarse.
4. El texto suelto con JSON, metadatos o vacío llegaba tal cual al cliente.
5. Los evals daban por resueltas esas respuestas y la caché guardaba los rechazos.

Con el modelo simulado se prueba el cableado; la medición con Groq real está en docs/calidad.md.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
import runner
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import prompt
from app.agent.grounding import check_grounding
from app.agent.loop import ESCALATION_MESSAGE, VERIFICATION_TALK
from app.agent.presentable import problem
from app.llm.cache import CachingProvider
from app.llm.types import LLMResponse, TokenUsage

SHIPPED = ['{"id": "1001", "shipped_at": "2026-11-05T09:30:00-03:00"}']


def _agent_calls(stack: SimulatedStack) -> int:
    return sum(1 for r in stack.claude_requests if "responder" in r["tool_names"])


# -- 1. hora sin cero adelante ---------------------------------------------------------------
def test_la_hora_y_el_dia_sin_cero_adelante_tienen_respaldo() -> None:
    ok = check_grounding("Salió el 5 de noviembre a las 9:30.", [], True, {}, SHIPPED, True)
    assert ok.ok, ok.reason
    assert not check_grounding("Salió a las 9:45.", [], True, {}, SHIPPED, True).ok
    assert not check_grounding("Salió a las 8:30.", [], True, {}, SHIPPED, True).ok


def test_el_estado_del_pedido_con_la_hora_de_despacho_no_deriva(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="hora_sin_cero")
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert result.action == "responder", result.escalation_summary
    assert "9:30" in result.message


# -- 2. verificación ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "No encontré el pedido 1003 entre los tuyos. Revisá el email de confirmación para "
        "verificarlo.",
        "¿Podés verificar el número de pedido?",
        "Verificá que la dirección esté completa.",
        "Te paso el código de seguimiento del envío.",
        "Ya verificamos el pago.",
        "El código postal de tu dirección es 1405.",
    ],
)
def test_hablar_de_verificar_otra_cosa_no_es_pedir_la_verificacion(text: str) -> None:
    assert VERIFICATION_TALK.search(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Para iniciar el reembolso necesitás verificar tu cuenta. Te enviamos un código de 6 "
        "dígitos a tu email.",
        "Te mandé un código a tu email.",
        "Necesito verificar tu identidad.",
        "Pasame el código que te llegó.",
        "Para seguir, tenés que verificarte.",
        "Ingresá el código de verificación.",
    ],
)
def test_pedir_el_codigo_o_verificar_la_identidad_sigue_contando(text: str) -> None:
    assert VERIFICATION_TALK.search(text) is not None


def test_un_pedido_ajeno_se_explica_aunque_diga_verificarlo(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="verificarlo")
    result = stack.send("lucia", "Pasame el estado del pedido 1003")
    assert result.action == "responder", result.escalation_summary
    assert "verificarlo" in result.message


# -- 3. consultar antes de modificar y explicar la regla ----------------------------------------
def test_el_prompt_pide_consultar_antes_de_modificar_y_explicar_la_regla() -> None:
    system = prompt.system_prompt("Acme Store", "rioplatense", "nombre: x")
    assert prompt.SYSTEM_PROMPT_VERSION == "system_v5"
    assert "Antes de modificar un pedido, consultalo" in system
    assert "regla del negocio" in system and "ofrecé una alternativa" in system


def test_un_cambio_de_direccion_de_un_pedido_despachado_se_explica_sin_derivar(
    conn: psycopg.Connection,
) -> None:
    """Lo que hizo gpt-oss-20b: cambia la dirección sin consultar y, ante el error, deriva."""
    stack = build_simulated_stack(conn, mode="modifica_sin_consultar")
    result = stack.send(
        "lucia", "Quiero cambiar la dirección del pedido 1001 a Av. Corrientes 1234, CABA"
    )
    assert result.action == "responder", result.escalation_summary
    assert "despachado" in result.message and "persona" in result.message
    assert [t.name for t in result.tool_calls] == ["consultar_pedido"]
    assert stack.acme_order("1001")["shipping_address"] == "Av. Rivadavia 4500, CABA"


# -- 4. nada impresentable llega al cliente ------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        '{"accion": "responder", "mensaje": "Tu pedido está despachado."}',
        '```json\n{"mensaje": "hola"}\n```',
        'Listo. {\n  "factual": true,\n  "fuentes": ["envios"],\n  tenés el reembolso',
        "Tu pedido está despachado.\n\nFuentes: [envios, seguimiento_de_pedidos]  factual: true",
        "Uso consultar_pedido para ver el estado.",
        "responder<|channel|>commentary",
    ],
)
def test_detecta_respuestas_impresentables(text: str) -> None:
    assert problem(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Tu pedido 1001 está *despachado*. ¿Te ayudo con algo más?",
        "Si querés, te puedo derivar con una persona del equipo para responder tu consulta.",
        "Te mandamos el código a maria_lopez@example.com.",
        "Abrimos de 10 a 20 horas: los domingos {cerrado} no.",
    ],
)
def test_una_respuesta_normal_es_presentable(text: str) -> None:
    assert problem(text) is None


def test_json_suelto_con_una_respuesta_valida_se_usa_sin_mostrar_el_json(
    conn: psycopg.Connection,
) -> None:
    stack = build_simulated_stack(conn, mode="json_suelto")
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert result.action == "responder"
    assert result.message.startswith("Tu pedido 1001 está despachado")
    assert problem(result.message) is None


@pytest.mark.parametrize("mode", ["metadatos_una_vez", "vacio_una_vez"])
def test_metadatos_o_vacio_se_reintentan_una_vez(conn: psycopg.Connection, mode: str) -> None:
    stack = build_simulated_stack(conn, mode=mode)
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert result.action == "responder" and problem(result.message) is None
    assert "despachado" in result.message
    assert _agent_calls(stack) == 3  # consulta, respuesta rota, reintento


@pytest.mark.parametrize("mode", ["metadatos", "vacio"])
def test_si_no_se_corrige_deriva_en_lugar_de_mandarlo(conn: psycopg.Connection, mode: str) -> None:
    stack = build_simulated_stack(conn, mode=mode)
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    assert result.action == "derivar" and result.message == ESCALATION_MESSAGE
    assert _agent_calls(stack) == 3  # un solo reintento


# -- 5. evals ------------------------------------------------------------------------------------
def test_los_evals_no_dan_por_resuelta_una_respuesta_impresentable(
    conn: psycopg.Connection,
) -> None:
    import json

    stack = build_simulated_stack(conn)
    cases = json.loads(runner.CASES_PATH.read_text(encoding="utf-8"))
    case = next(c for c in cases if c["id"] == "01_donde_esta_mi_pedido")
    result = runner.send_case(stack, case)
    good = runner.grade(case, result, set())
    assert good.resolved and good.checks["presentable"]
    for bad in ('{"mensaje": "Tu pedido está despachado, llega el 10/11."}', "", "  "):
        graded = runner.grade(case, replace(result, message=bad), set())
        assert not graded.resolved and not graded.checks["presentable"]


class _Refusing:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text="",
            model="openai/gpt-oss-20b",
            stop_reason="refusal",
            usage=TokenUsage(0, 0),
            content=[],
            tool_calls=[],
        )


def test_la_cache_no_guarda_los_rechazos(tmp_path: Path) -> None:
    """Un rechazo de Groq (tres salidas inválidas) se repetía en cada corrida desde la caché."""
    inner = _Refusing()
    request = {"model": "m", "messages": [{"role": "user", "content": "x"}], "max_tokens": 10}
    CachingProvider(inner, tmp_path, profile="groq:a").create(**request)
    CachingProvider(inner, tmp_path, profile="groq:a").create(**request)
    assert inner.calls == 2
    assert list(tmp_path.iterdir()) == []
