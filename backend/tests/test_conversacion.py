"""Calidad — el agente responde solo, sin derivar, la charla y las consultas generales.

Saludos, agradecimientos, charla, "¿sos un bot?", "¿qué podés hacer?" y preguntas fuera de tema
se responden amable y breve, volviendo a lo que el agente puede ayudar (docs/calidad.md). Solo
se deriva si el cliente pide una persona, si hay un problema que no puede resolver o si la
información no está en los documentos. Nunca inventa: en la charla no da datos de la empresa.
"""

import uuid
from typing import Any

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent.classifier import CHAT, GENERAL, classify
from app.llm.types import LLMResponse, TokenUsage, ToolCall


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


@pytest.mark.parametrize(
    "text",
    ["hola", "buenasss", "gracias!!", "sos un bot?", "q podes hacer", "quien gano el partido ayer"],
)
def test_la_charla_se_responde_sin_derivar(stack: SimulatedStack, text: str) -> None:
    result = stack.send("lucia", text)
    assert result.action == "responder" and result.escalation_summary is None
    assert result.message.strip()


def test_en_la_charla_no_hay_herramientas_ni_derivacion_y_se_cuenta_que_puede_hacer(
    stack: SimulatedStack,
) -> None:
    stack.send("lucia", "q podes hacer")
    request = stack.claude_requests[-1]
    assert request["tool_names"] == {"responder"}  # ni herramientas de negocio ni derivar
    system = request["body"]["system"]
    system_text = system if isinstance(system, str) else " ".join(b["text"] for b in system)
    assert "Informar el estado y la fecha estimada de entrega" in system_text  # sus objetivos


def test_una_consulta_general_sin_tipo_propio_se_responde_con_los_documentos(
    stack: SimulatedStack,
) -> None:
    result = stack.send("sofia", "cuanto tarda un envio a mendoza?")  # sofía no está verificada
    assert result.action == "responder" and not result.verification_requested
    assert "envios" in result.sources
    assert result.case_type is None


def test_pedir_una_persona_sigue_derivando(stack: SimulatedStack) -> None:
    assert stack.send("lucia", "quiero hablar con una persona").action == "derivar"


class CaptureLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(
            text="",
            model="claude-haiku-5-5",
            stop_reason="tool_use",
            usage=TokenUsage(1, 1),
            tool_calls=[ToolCall("t1", "clasificar", {"tipo": CHAT})],
        )


def test_el_clasificador_distingue_charla_y_consulta_general_y_ve_el_turno_anterior() -> None:
    llm = CaptureLLM()
    result = classify(
        llm,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        company="Acme Store",
        message="el 1001",
        case_types={"donde_esta_mi_pedido": "Informar el estado de un pedido"},
        previous="¿Me pasás el número de pedido?",
    )
    assert result.label == CHAT
    call = llm.calls[0]
    labels = call["tools"][0]["input_schema"]["properties"]["tipo"]["enum"]
    assert CHAT in labels and GENERAL in labels
    content = call["messages"][0]["content"]
    assert "¿Me pasás el número de pedido?" in content and "<mensaje_cliente>el 1001" in content
