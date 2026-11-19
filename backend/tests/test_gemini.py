"""Proveedor Gemini detrás de LLMClient: test de aceptación.

El agente sigue hablando en el formato de mensajes de Claude; el proveedor lo traduce a la API
REST de Gemini y de vuelta. Se prueba sobre un transporte HTTP simulado, sin red ni claves.
"""

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.llm.client import AnthropicProvider, LLMClient
from app.llm.embedder import VoyageEmbedder
from app.llm.factory import embedder_from_env, provider_from_env
from app.llm.gemini import (
    GeminiEmbedder,
    GeminiProvider,
    GeminiQuotaExhausted,
    GeminiTransport,
)
from app.llm.types import TokenUsage, UsageKind

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los tests usan los modelos por defecto aunque el .env local elija otros, y sin Groq
    (con su clave, el factory arma un respaldo)."""
    for key in ("GEMINI_AGENT_MODEL", "GEMINI_CLASSIFIER_MODEL", "GROQ_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)


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
        api_key="test",
        http_client=httpx.Client(transport=_transport(replies, calls)),
        min_interval=0,
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


def test_una_llamada_que_no_hizo_gemini_lleva_la_firma_de_reemplazo() -> None:
    """Groq hizo la llamada antes del respaldo (o vino de la caché): sin firma, Gemini 3
    responde 400 "Function call is missing a thought_signature"."""
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = GeminiProvider(_gemini([TEXT_REPLY], calls))
    provider.create(
        model="claude-sonnet-5-5",
        messages=[
            {"role": "user", "content": "¿Dónde está mi pedido 1001?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "fc_groq_1",
                        "name": "consultar_pedido",
                        "input": {"pedido": "1001"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "fc_groq_1", "content": "{}"}],
            },
        ],
        max_tokens=1024,
        tools=[CONSULTAR],
    )
    part = calls[0][1]["contents"][1]["parts"][0]
    assert part["functionCall"]["name"] == "consultar_pedido"
    assert part["thoughtSignature"] == "skip_thought_signature_validator"


def test_embedder_gemini_marca_documentos_y_consultas_y_registra_uso() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def reply(n: int, tokens: int) -> dict[str, Any]:
        return {
            "embeddings": [{"values": [0.1] * 1024} for _ in range(n)],
            "usageMetadata": {"promptTokenCount": tokens},
        }

    recorder = MemoryRecorder()
    embedder = GeminiEmbedder(recorder, _gemini([reply(100, 700), reply(1, 9), reply(1, 4)], calls))
    assert embedder.dimensions == 1024  # igual que la columna vector(1024)

    docs = embedder.embed([f"texto {i}" for i in range(101)], tenant_id=TENANT)
    query = embedder.embed(["¿cuánto tarda el envío?"], tenant_id=TENANT, input_type="query")

    assert len(docs) == 101 and len(query[0]) == 1024
    url, body = calls[0]
    assert url.endswith("/models/gemini-embedding-2:batchEmbedContents")
    assert len(body["requests"]) == 100  # máximo por lote; el resto va en otro pedido
    assert body["requests"][0] == {
        "model": "models/gemini-embedding-2",
        "content": {"parts": [{"text": "title: none | text: texto 0"}]},
        "outputDimensionality": 1024,
    }
    assert calls[2][1]["requests"][0]["content"] == {
        "parts": [{"text": "task: search result | query: ¿cuánto tarda el envío?"}]
    }
    assert [(r["kind"], r["model"], r["usage"].input_tokens, r["cost"]) for r in recorder.rows] == [
        ("embedding", "gemini-embedding-2", 709, Decimal(0)),
        ("embedding", "gemini-embedding-2", 4, Decimal(0)),
    ]


def test_elige_proveedor_y_embedder_por_las_claves_del_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        provider_from_env()

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert isinstance(provider_from_env(), GeminiProvider)
    assert isinstance(embedder_from_env(MemoryRecorder()), GeminiEmbedder)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert isinstance(provider_from_env(), AnthropicProvider)
    assert isinstance(embedder_from_env(MemoryRecorder()), GeminiEmbedder)  # sin Voyage

    monkeypatch.setenv("VOYAGE_API_KEY", "v")
    assert isinstance(embedder_from_env(MemoryRecorder()), VoyageEmbedder)


def _quota_error(quota_id: str, retry_delay: str | None = None) -> httpx.Response:
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        }
    ]
    if retry_delay:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return httpx.Response(429, json=body)


def _scripted(
    replies: list[httpx.Response], sleeps: list[float], **kwargs: Any
) -> tuple[GeminiProvider, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return replies[len(calls) - 1]

    transport = GeminiTransport(
        api_key="test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0,
        sleep=sleeps.append,
        **kwargs,
    )
    return GeminiProvider(transport), calls


HOLA = [{"role": "user", "content": "hola"}]
PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
PER_DAY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"


def test_reintenta_ante_429_y_5xx_con_la_espera_que_pide_la_api() -> None:
    sleeps: list[float] = []
    provider, calls = _scripted(
        [
            _quota_error(PER_MINUTE, "17s"),
            httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}}),
            httpx.Response(200, json=TEXT_REPLY),
        ],
        sleeps,
    )
    response = provider.create(model="claude-sonnet-5-5", messages=HOLA, max_tokens=10)
    assert response.text == "Tu pedido está en camino."
    assert len(calls) == 3
    assert sleeps[0] == 17.0  # la espera que indica RetryInfo
    assert 4.0 <= sleeps[1] <= 5.0  # espera exponencial (segundo intento) con jitter


def test_cuota_diaria_agotada_no_reintenta() -> None:
    sleeps: list[float] = []
    provider, calls = _scripted([_quota_error(PER_DAY, "40000s")], sleeps)
    with pytest.raises(GeminiQuotaExhausted) as error:
        provider.create(model="claude-sonnet-5-5", messages=HOLA, max_tokens=10)
    assert error.value.daily
    assert len(calls) == 1 and sleeps == []


def test_429_persistente_agota_los_reintentos() -> None:
    sleeps: list[float] = []
    provider, calls = _scripted([_quota_error(PER_MINUTE)] * 3, sleeps, max_retries=2)
    with pytest.raises(GeminiQuotaExhausted) as error:
        provider.create(model="claude-sonnet-5-5", messages=HOLA, max_tokens=10)
    assert not error.value.daily
    assert len(calls) == 3 and len(sleeps) == 2


def test_pausa_minima_entre_llamadas() -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    transport = GeminiTransport(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=TEXT_REPLY))
        ),
        min_interval=6,
        sleep=sleep,
        clock=lambda: now[0],
    )
    provider = GeminiProvider(transport)
    provider.create(model="claude-sonnet-5-5", messages=HOLA, max_tokens=10)
    now[0] += 2.5  # el agente tarda 2,5 s en armar el siguiente pedido
    provider.create(model="claude-sonnet-5-5", messages=HOLA, max_tokens=10)
    assert sleeps == [3.5]


def test_los_modelos_del_agente_y_del_clasificador_se_eligen_por_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_AGENT_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_CLASSIFIER_MODEL", "gemini-3.6-flash")
    calls: list[tuple[str, dict[str, Any]]] = []
    provider = GeminiProvider(_gemini([TEXT_REPLY, TEXT_REPLY], calls))
    agent = provider.create(model="claude-sonnet-5-5", messages=HOLA, max_tokens=10)
    provider.create(model="claude-haiku-5-5", messages=HOLA, max_tokens=10)
    assert calls[0][0].endswith("/models/gemini-3.8-flash:generateContent")
    assert calls[1][0].endswith("/models/gemini-3.6-flash:generateContent")
    assert agent.model == "gemini-3.8-flash"  # así queda en `usage`

    monkeypatch.setenv("GEMINI_AGENT_MODEL", "gemini-modelo-inventado")
    with pytest.raises(ValueError, match="sin precio"):
        GeminiProvider(_gemini([], calls))


def test_haiku_usa_flash_lite_y_un_bloqueo_es_rechazo() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {}}
    response = GeminiProvider(_gemini([blocked], calls)).create(
        model="claude-haiku-5-5", messages=[{"role": "user", "content": "hola"}], max_tokens=10
    )
    assert calls[0][0].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert response.stop_reason == "refusal"
    assert response.text == ""
