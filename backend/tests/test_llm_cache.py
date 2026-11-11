"""Caché de respuestas por entrada exacta (evals y corridas largas del onboarding).

Con la cuota diaria de los modelos gratuitos, una corrida larga puede cortarse: al retomarla,
las llamadas ya hechas salen de la caché sin gastar tokens. Otro perfil de modelos, otra entrada.
"""

from pathlib import Path
from typing import Any

from app.llm.cache import CachingProvider
from app.llm.types import LLMResponse, TokenUsage, ToolCall


class Counting:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text="hola",
            model="openai/gpt-oss-20b",
            stop_reason="tool_use",
            usage=TokenUsage(100, 20),
            content=[{"type": "tool_use", "id": "t1", "name": "responder", "input": {"a": 1}}],
            tool_calls=[ToolCall("t1", "responder", {"a": 1})],
        )


def test_repite_de_la_cache_sin_gastar_y_separa_por_perfil(tmp_path: Path) -> None:
    inner = Counting()
    request = {
        "model": "claude-sonnet-5-5",
        "messages": [{"role": "user", "content": "x"}],
        "max_tokens": 10,
    }
    first = CachingProvider(inner, tmp_path, profile="groq:a").create(**request)
    again = CachingProvider(inner, tmp_path, profile="groq:a").create(**request)
    assert inner.calls == 1
    assert again.text == first.text and again.tool_calls == first.tool_calls
    assert again.usage == TokenUsage(0, 0)  # de la caché: no hubo gasto
    CachingProvider(inner, tmp_path, profile="groq:b").create(**request)
    assert inner.calls == 2
