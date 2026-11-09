"""Proveedor Gemini detrás de LLMClient: test de aceptación.

El agente sigue hablando en el formato de mensajes de Claude; el proveedor lo traduce a la API
REST de Gemini y de vuelta. Se prueba sobre un transporte HTTP simulado, sin red ni claves.
"""

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx

from app.llm.client import LLMClient
from app.llm.gemini import GeminiProvider, GeminiTransport
from app.llm.types import TokenUsage, UsageKind

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


class MemoryRecorder:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(
        self,
        *,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID | None,
        kind: UsageKind,
        model: str,
        usage: TokenUsage,
        cost_usd: Decimal,
    ) -> None:
        self.rows.append({"kind": kind, "model": model, "usage": usage, "cost": cost_usd})


def _transport(
    replies: list[dict[str, Any]], calls: list[tuple[str, dict[str, Any]]]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test"
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json=replies[len(calls) - 1])

    return httpx.MockTransport(handler)


def _gemini(
    replies: list[dict[str, Any]], calls: list[tuple[str, dict[str, Any]]]
) -> GeminiTransport:
    return GeminiTransport(
        api_key="test", http_client=httpx.Client(transport=_transport(replies, calls))
    )


CONSULTAR = {
    "name": "consultar_pedido",
    "description": "Consulta un pedido.",
    "input_schema": {
        "type": "object",
        "properties": {"pedido": {"type": "string"}},
        "required": ["pedido"],
        "additionalProperties": False,
    },
}

FUNCTION_CALL_REPLY = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "id": "call_1",
                            "name": "consultar_pedido",
                            "args": {"pedido": "1001"},
                        },
                        "thoughtSignature": "firma-1",
                    }
                ],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 900,
        "cachedContentTokenCount": 100,
        "candidatesTokenCount": 20,
        "thoughtsTokenCount": 30,
    },
    "modelVersion": "gemini-3.6-flash",
}

TEXT_REPLY = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "Tu pedido está en camino."}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 8},
}


def test_traduce_herramientas_y_respuesta_al_formato_del_agente() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    recorder = MemoryRecorder()
    llm = LLMClient(GeminiProvider(_gemini([FUNCTION_CALL_REPLY], calls)), recorder)

    response = llm.complete(
        tenant_id=TENANT,
        model="claude-sonnet-5-5",
        system="Sos el asistente de Acme.",
        messages=[{"role": "user", "content": "¿Dónde está mi pedido 1001?"}],
        tools=[CONSULTAR],
        max_tokens=4096,
        output_config={"effort": "low"},
    )

    url, body = calls[0]
    assert url.endswith("/models/gemini-3.6-flash:generateContent")  # Sonnet -> Flash gratuito
    assert body["systemInstruction"] == {"parts": [{"text": "Sos el asistente de Acme."}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "¿Dónde está mi pedido 1001?"}]}
    ]
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "consultar_pedido",
                    "description": "Consulta un pedido.",
                    "parametersJsonSchema": CONSULTAR["input_schema"],
                }
            ]
        }
    ]
    assert body["generationConfig"] == {
        "maxOutputTokens": 4096,
        "thinkingConfig": {"thinkingLevel": "LOW"},
    }

    assert response.stop_reason == "tool_use"
    assert response.model == "gemini-3.6-flash"
    assert [(c.id, c.name, c.input) for c in response.tool_calls] == [
        ("call_1", "consultar_pedido", {"pedido": "1001"})
    ]
    assert response.usage == TokenUsage(
        input_tokens=800, output_tokens=50, cache_read_input_tokens=100
    )
    # El uso queda registrado con el modelo que respondió, sin costo en el nivel gratuito.
    assert [(r["kind"], r["model"], r["cost"]) for r in recorder.rows] == [
        ("llm", "gemini-3.6-flash", Decimal(0))
    ]


def test_devuelve_resultados_y_firma_de_pensamiento_en_el_turno_siguiente() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = GeminiProvider(_gemini([FUNCTION_CALL_REPLY, TEXT_REPLY], calls))
    first = provider.create(
        model="claude-sonnet-5-5",
        messages=[{"role": "user", "content": "¿Dónde está mi pedido 1001?"}],
        max_tokens=1024,
        tools=[CONSULTAR],
    )
    messages = [
        {"role": "user", "content": "¿Dónde está mi pedido 1001?"},
        {"role": "assistant", "content": first.content},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": json.dumps({"estado": "en_camino"}),
                }
            ],
        },
    ]
    second = provider.create(
        model="claude-sonnet-5-5", messages=messages, max_tokens=1024, tools=[CONSULTAR]
    )

    assert calls[1][1]["contents"][1:] == [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "id": "call_1",
                        "name": "consultar_pedido",
                        "args": {"pedido": "1001"},
                    },
                    "thoughtSignature": "firma-1",
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "id": "call_1",
                        "name": "consultar_pedido",
                        "response": {"estado": "en_camino"},
                    }
                }
            ],
        },
    ]
    assert second.text == "Tu pedido está en camino."
    assert second.stop_reason == "end_turn"
    assert second.tool_calls == []


def test_haiku_usa_flash_lite_y_un_bloqueo_es_rechazo() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {}}
    response = GeminiProvider(_gemini([blocked], calls)).create(
        model="claude-haiku-5-5", messages=[{"role": "user", "content": "hola"}], max_tokens=10
    )
    assert calls[0][0].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert response.stop_reason == "refusal"
    assert response.text == ""
