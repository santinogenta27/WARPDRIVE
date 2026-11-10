"""Caché de respuestas del modelo por entrada exacta (spec: "caché por entrada exacta").

La usan los evals (no pagar dos veces la misma llamada) y las corridas largas del onboarding:
con la cuota diaria de los modelos gratuitos, una corrida puede cortarse, y al retomarla las
llamadas ya hechas salen de acá sin gastar tokens. Un acierto devuelve uso cero.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.llm.client import LLMProvider
from app.llm.types import LLMResponse, TokenUsage, ToolCall


class CachingProvider:
    """`profile` identifica los modelos reales detrás de los nombres que pide el agente (p. ej.
    qué modelo de Groq reemplaza a claude-sonnet-5-5): otro perfil, otra entrada de caché."""

    def __init__(self, inner: LLMProvider, directory: Path, *, profile: str = "") -> None:
        self._inner = inner
        self._dir = directory
        self._profile = profile
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        request = {
            **({"profile": self._profile} if self._profile else {}),
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "system": system,
            **options,
        }
        key = hashlib.sha256(
            json.dumps(request, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        path = self._dir / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return LLMResponse(
                text=data["text"],
                model=data["model"],
                stop_reason=data["stop_reason"],
                usage=TokenUsage(input_tokens=0, output_tokens=0),
                content=data["content"],
                tool_calls=[ToolCall(**call) for call in data["tool_calls"]],
            )
        response = self._inner.create(
            model=model, messages=messages, max_tokens=max_tokens, system=system, **options
        )
        payload = {
            "text": response.text,
            "model": response.model,
            "stop_reason": response.stop_reason,
            "content": response.content,
            "tool_calls": [asdict(call) for call in response.tool_calls],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return response
