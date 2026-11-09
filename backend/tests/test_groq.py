"""Groq (API compatible con OpenAI) detrás de LLMClient, y respaldo entre proveedores.

El agente habla en el formato de Claude; GroqProvider lo traduce a chat completions y de vuelta.
Si el proveedor principal se queda sin cuota (Gemini gratis), FallbackProvider sigue con el otro
para que la demo no se corte. Todo con transportes simulados: sin red ni claves.
"""

import json
from typing import Any

import httpx
import pytest

from app.llm.client import AnthropicProvider
from app.llm.errors import QuotaExhausted
from app.llm.factory import llm_backend, provider_from_env
from app.llm.fallback import FallbackProvider
from app.llm.gemini import GeminiProvider, GeminiQuotaExhausted
from app.llm.groq import GroqProvider, GroqQuotaExhausted, GroqTransport
from app.llm.pricing import PRICES
from app.llm.types import LLMResponse, TokenUsage

ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "LLM_PROVIDER",
    "GROQ_AGENT_MODEL",
    "GROQ_CLASSIFIER_MODEL",
    "GEMINI_AGENT_MODEL",
    "GEMINI_CLASSIFIER_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def completion(message: dict[str, Any], finish: str = "stop", **usage: int) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, **usage},
    }


class FakeGroq:
    """Chat completions simulado: devuelve las respuestas en orden y guarda los pedidos."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.sleeps: list[float] = []

    def transport(self, **options: Any) -> GroqTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://api.groq.com/openai/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer clave-groq"
            self.requests.append(json.loads(request.content))
            return self.responses.pop(0)

        return GroqTransport(
            api_key="clave-groq",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=self.sleeps.append,
            **options,
        )


TOOLS = [
    {
        "name": "consultar_pedido",
        "description": "Consulta un pedido",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    }
]


def test_traduce_el_turno_del_agente_al_formato_de_groq() -> None:
    fake = FakeGroq(httpx.Response(200, json=completion({"role": "assistant", "content": "ok"})))
    provider = GroqProvider(transport=fake.transport())
    messages = [
        {"role": "user", "content": "¿Dónde está mi pedido 1001?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Lo busco."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "consultar_pedido",
                    "input": {"order_id": "1001"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": json.dumps({"ok": True, "data": {"status": "shipped"}}),
                },
            ],
        },
    ]
    response = provider.create(
        model="claude-sonnet-5-5",
        messages=messages,
        max_tokens=4096,
        system="Sos el asistente de Acme.",
        tools=TOOLS,
        tool_choice={"type": "any"},
        output_config={"effort": "low"},
    )
    body = fake.requests[0]
    assert body["model"] == "openai/gpt-oss-120b"  # agente por defecto
    assert body["max_completion_tokens"] == 4096
    assert body["reasoning_effort"] == "low"
    assert body["tool_choice"] == "required"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "consultar_pedido",
                "description": "Consulta un pedido",
                "parameters": TOOLS[0]["input_schema"],
            },
        }
    ]
    assert body["messages"] == [
        {"role": "system", "content": "Sos el asistente de Acme."},
        {"role": "user", "content": "¿Dónde está mi pedido 1001?"},
        {
            "role": "assistant",
            "content": "Lo busco.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "consultar_pedido", "arguments": '{"order_id": "1001"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "consultar_pedido",
            "content": '{"ok": true, "data": {"status": "shipped"}}',
        },
    ]
    assert response.text == "ok" and response.stop_reason == "end_turn"


def test_traduce_las_llamadas_a_herramientas_de_la_respuesta() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "reasoning": "pensamiento interno",
        "tool_calls": [
            {
                "id": "call_d5wg",
                "type": "function",
                "function": {"name": "consultar_pedido", "arguments": '{"order_id": "1001"}'},
            }
        ],
    }
    data = completion(message, "tool_calls")
    data["usage"]["prompt_tokens_details"] = {"cached_tokens": 20}
    fake = FakeGroq(httpx.Response(200, json=data))
    response = GroqProvider(transport=fake.transport()).create(
        model="claude-haiku-5-5", messages=[{"role": "user", "content": "hola"}], max_tokens=50
    )
    assert fake.requests[0]["model"] == "openai/gpt-oss-20b"  # clasificador por defecto
    assert response.stop_reason == "tool_use"
    assert [(c.id, c.name, c.input) for c in response.tool_calls] == [
        ("call_d5wg", "consultar_pedido", {"order_id": "1001"})
    ]
    assert response.content == [
        {
            "type": "tool_use",
            "id": "call_d5wg",
            "name": "consultar_pedido",
            "input": {"order_id": "1001"},
        }
    ]
    assert response.usage == TokenUsage(
        input_tokens=100, output_tokens=30, cache_read_input_tokens=20
    )
    assert response.model == "openai/gpt-oss-120b"


def test_los_modelos_se_eligen_por_entorno_y_tienen_precio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_AGENT_MODEL", "qwen/qwen3.8-27b")
    monkeypatch.setenv("GROQ_CLASSIFIER_MODEL", "openai/gpt-oss-120b")
    fake = FakeGroq(*(httpx.Response(200, json=completion({"content": "ok"})) for _ in range(2)))
    provider = GroqProvider(transport=fake.transport())
    provider.create(
        model="claude-sonnet-5-5", messages=[], max_tokens=10, output_config={"effort": "low"}
    )
    provider.create(
        model="claude-haiku-5-5", messages=[], max_tokens=10, output_config={"effort": "low"}
    )
    assert [r["model"] for r in fake.requests] == ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    assert "reasoning_effort" not in fake.requests[0]  # solo los modelos gpt-oss lo aceptan
    assert fake.requests[1]["reasoning_effort"] == "low"  # el clasificador pide poco esfuerzo
    for model in ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"):
        assert model in PRICES
    monkeypatch.setenv("GROQ_AGENT_MODEL", "modelo-inventado")
    with pytest.raises(ValueError, match="sin precio"):
        GroqProvider(transport=fake.transport())


def test_reintenta_ante_429_y_5xx_con_la_espera_que_pide_la_api() -> None:
    fake = FakeGroq(
        httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "rate"}}),
        httpx.Response(503, json={"error": {"message": "over capacity"}}),
        httpx.Response(200, json=completion({"content": "ok"})),
    )
    response = GroqProvider(transport=fake.transport()).create(
        model="claude-sonnet-5-5", messages=[], max_tokens=10
    )
    assert response.text == "ok" and len(fake.requests) == 3
    assert fake.sleeps[0] == 2.0 and len(fake.sleeps) == 2


def test_una_espera_larga_es_cuota_agotada_y_no_reintenta() -> None:
    fake = FakeGroq(
        httpx.Response(429, headers={"retry-after": "3600"}, json={"error": {"message": "RPD"}})
    )
    with pytest.raises(GroqQuotaExhausted) as raised:
        GroqProvider(transport=fake.transport()).create(
            model="claude-sonnet-5-5", messages=[], max_tokens=10
        )
    assert isinstance(raised.value, QuotaExhausted) and raised.value.daily
    assert fake.sleeps == []


def test_respeta_los_tokens_por_minuto_de_cada_modelo() -> None:
    """Con pocos tokens restantes del minuto, espera a que se renueven antes de llamar."""
    tight = {"x-ratelimit-remaining-tokens": "900", "x-ratelimit-reset-tokens": "7.5s"}
    fake = FakeGroq(
        httpx.Response(200, headers=tight, json=completion({"content": "uno"})),
        httpx.Response(200, json=completion({"content": "dos"})),
        httpx.Response(200, json=completion({"content": "tres"})),
    )
    provider = GroqProvider(transport=fake.transport(min_tokens=3000))
    provider.create(model="claude-sonnet-5-5", messages=[], max_tokens=10)
    provider.create(model="claude-haiku-5-5", messages=[], max_tokens=10)  # otro modelo: no espera
    assert fake.sleeps == []
    provider.create(model="claude-sonnet-5-5", messages=[], max_tokens=10)
    assert fake.sleeps == [7.5]


def test_reintenta_una_llamada_a_herramienta_mal_generada() -> None:
    failed = {
        "error": {
            "message": "Invalid tool call generated",
            "code": "tool_use_failed",
            "type": "invalid_request_error",
        }
    }
    fake = FakeGroq(
        httpx.Response(400, json=failed),
        httpx.Response(200, json=completion({"content": "ok"})),
    )
    response = GroqProvider(transport=fake.transport()).create(
        model="claude-sonnet-5-5", messages=[], max_tokens=10, tools=TOOLS
    )
    assert response.text == "ok" and len(fake.requests) == 2


class Scripted:
    """Proveedor de prueba: lanza o responde según su guion."""

    def __init__(self, name: str, *outcomes: Exception | str) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(
            text=outcome, model=self.name, stop_reason="end_turn", usage=TokenUsage(1, 1)
        )


def test_si_el_principal_se_queda_sin_cuota_sigue_con_el_respaldo() -> None:
    now = [0.0]
    gemini = Scripted("gemini", GeminiQuotaExhausted("sin cuota", daily=True), "de gemini")
    groq = Scripted("groq", "de groq", "otra de groq")
    provider = FallbackProvider(gemini, groq, clock=lambda: now[0])

    assert provider.create(model="claude-sonnet-5-5", messages=[], max_tokens=10).text == "de groq"
    # Mientras dura la pausa no se vuelve a intentar con el principal.
    assert provider.create(model="claude-sonnet-5-5", messages=[], max_tokens=10).text == (
        "otra de groq"
    )
    assert gemini.calls == 1
    now[0] += 3601  # pasada la pausa de una cuota diaria, vuelve al principal
    assert provider.create(model="claude-sonnet-5-5", messages=[], max_tokens=10).text == (
        "de gemini"
    )


def test_otros_errores_del_principal_no_cambian_de_proveedor() -> None:
    gemini = Scripted("gemini", ValueError("pedido inválido"))
    groq = Scripted("groq", "de groq")
    with pytest.raises(ValueError):
        FallbackProvider(gemini, groq).create(model="claude-sonnet-5-5", messages=[], max_tokens=10)
    assert groq.calls == 0


def test_elige_proveedor_y_respaldo_por_las_claves_del_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        provider_from_env()

    monkeypatch.setenv("GROQ_API_KEY", "q")
    assert llm_backend() == "groq" and isinstance(provider_from_env(), GroqProvider)

    monkeypatch.setenv("GEMINI_API_KEY", "g")  # Gemini principal, Groq de respaldo
    provider = provider_from_env()
    assert llm_backend() == "gemini" and isinstance(provider, FallbackProvider)
    assert isinstance(provider.primary, GeminiProvider)
    assert isinstance(provider.secondary, GroqProvider)

    monkeypatch.setenv("LLM_PROVIDER", "groq")  # se puede invertir
    provider = provider_from_env()
    assert llm_backend() == "groq" and isinstance(provider, FallbackProvider)
    assert isinstance(provider.primary, GroqProvider)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")  # con Claude no hay respaldo gratuito
    assert isinstance(provider_from_env(), AnthropicProvider)
