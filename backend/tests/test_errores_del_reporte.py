"""Hito 6, etapa 2 — los tres errores reales que quedaron en el reporte del hito 5
(decisión 027):

1. Un cambio de dirección sin la dirección nueva derivaba en lugar de pedirla.
2. La consulta por Mercado Pago derivaba aunque los documentos cubren los medios de pago.
3. La amenaza de denuncia no frenaba: el agente respondía en lugar de derivar.

El 3 se resuelve en código y acá queda probado. El 1 y el 2 dependen del modelo: acá se prueba
el cableado con el modelo simulado; la medición con Groq real está en docs/calidad.md.
"""

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import prompt
from app.agent.loop import ESCALATION_MESSAGE, LEGAL_THREAT
from app.onboarding.discover import instructions_yaml


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def test_un_cambio_de_direccion_sin_la_direccion_nueva_la_pide(stack: SimulatedStack) -> None:
    result = stack.send("lucia", "Me mudé, ¿se puede mandar el pedido 1002 a otra dirección?")
    assert result.action == "responder" and "dirección" in result.message
    assert [t.name for t in result.tool_calls] == ["consultar_pedido"]  # no cambió nada
    assert stack.acme_order("1002")["shipping_address"] == "Calle Los Aromos 123, Rosario"


def test_la_consulta_por_un_medio_de_pago_se_responde_con_los_documentos(
    stack: SimulatedStack,
) -> None:
    result = stack.send("lucia", "¿Puedo pagar con Mercado Pago?")
    assert result.action == "responder" and result.sources == ["medios_de_pago_y_cuotas"]


@pytest.mark.parametrize(
    "text",
    [
        "Devolveme $500.000 ahora mismo o hago la denuncia",
        "los voy a denunciar en defensa del consumidor",
        "Si no me responden hoy hablo con mi abogado",
        "les mando una carta documento",
    ],
)
def test_una_amenaza_legal_deriva_siempre_sin_consultar_al_modelo(
    stack: SimulatedStack, text: str
) -> None:
    result = stack.send("martin", text)
    assert result.action == "derivar" and result.message == ESCALATION_MESSAGE
    assert result.tool_calls == [] and result.approval_id is None
    assert result.escalation_summary is not None
    assert "legal" in result.escalation_summary["motivo"]
    assert stack.claude_requests == []  # lo decide el código, no el modelo


@pytest.mark.parametrize(
    "text",
    ["¿Dónde está mi pedido 1001?", "quiero demandar... digo, pedir un cambio", "hola"],
)
def test_una_consulta_comun_no_se_toma_por_amenaza(text: str) -> None:
    assert (LEGAL_THREAT.search(text) is not None) == ("demandar" in text)


def test_el_prompt_y_las_instrucciones_piden_el_dato_que_falta() -> None:
    system = prompt.system_prompt("Acme Store", "rioplatense", "nombre: x")
    assert prompt.SYSTEM_PROMPT_VERSION == "system_v4"
    assert "No derives por un dato que falta" in system
    assert "no derives por eso" in system
    # Los tipos que descubre el onboarding ya no nacen con "si falta información, derivá".
    generated = instructions_yaml("cambio_direccion", "Modificar la dirección de entrega")
    assert "pedíselo" in generated and "Si falta información" not in generated
