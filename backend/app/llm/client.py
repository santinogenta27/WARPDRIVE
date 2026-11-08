"""LLMClient: único punto de entrada a los modelos. Toda llamada registra tokens y costo."""

import uuid
from decimal import Decimal
from typing import Any, Protocol

import anthropic
import psycopg

from app.db.connection import tenant_scope
from app.llm.pricing import PRICES, cost_usd
from app.llm.types import LLMResponse, TokenUsage, ToolCall, UsageKind, UsageRecorder


class LLMProvider(Protocol):
    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse: ...


class AnthropicProvider:
    """Proveedor Claude con el SDK oficial. Timeout de 30 s y 3 reintentos (spec: fallas)."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(timeout=30.0, max_retries=3)

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system is not None:
            params["system"] = system
        params.update(options)
        message = self._client.messages.create(**params)
        text = "".join(block.text for block in message.content if block.type == "text")
        u = message.usage
        usage = TokenUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
        )
        return LLMResponse(
            text=text,
            model=message.model,
            stop_reason=message.stop_reason,
            usage=usage,
            content=[block.model_dump(mode="json", exclude_none=True) for block in message.content],
            tool_calls=[
                ToolCall(id=block.id, name=block.name, input=dict(block.input))
                for block in message.content
                if block.type == "tool_use"
            ],
            raw=message,
        )


class PostgresUsageRecorder:
    """Inserta en `usage` dentro de la transacción del llamador, con el alcance del tenant."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

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
        with tenant_scope(self._conn, tenant_id):
            self._conn.execute(
                "insert into usage (tenant_id, case_id, kind, model, input_tokens, output_tokens,"
                " cache_creation_input_tokens, cache_read_input_tokens, cost_usd)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant_id,
                    case_id,
                    kind,
                    model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_creation_input_tokens,
                    usage.cache_read_input_tokens,
                    cost_usd,
                ),
            )


class LLMClient:
    def __init__(self, provider: LLMProvider, recorder: UsageRecorder) -> None:
        self._provider = provider
        self._recorder = recorder

    def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        case_id: uuid.UUID | None = None,
        **options: Any,
    ) -> LLMResponse:
        if model not in PRICES:  # sin precio no hay costo: no se llama
            raise ValueError(f"Modelo sin precio configurado: {model}")
        response = self._provider.create(
            model=model, messages=messages, max_tokens=max_tokens, system=system, **options
        )
        self._recorder.record(
            tenant_id=tenant_id,
            case_id=case_id,
            kind="llm",
            model=model,
            usage=response.usage,
            cost_usd=cost_usd(model, response.usage),
        )
        return response
