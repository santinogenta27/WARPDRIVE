"""LLMClient: único punto de entrada a los modelos. Toda llamada registra tokens y costo."""

import uuid
from decimal import Decimal
from typing import Any, Protocol

import anthropic
import psycopg

from app.db.connection import tenant_scope
from app.llm.pricing import PRICES, cost_usd
from app.llm.types import LLMResponse, TokenUsage, ToolCall, UsageKind, UsageRecorder
from app.obs import timing


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
        events: list[str] | None = None,
    ) -> None:
        with tenant_scope(self._conn, tenant_id):
            self._conn.execute(
                "insert into usage (tenant_id, case_id, kind, model, input_tokens, output_tokens,"
                " cache_creation_input_tokens, cache_read_input_tokens, cost_usd, events)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    events or [],
                ),
            )


class LLMClient:
    def __init__(self, provider: LLMProvider, recorder: UsageRecorder) -> None:
        self._provider = provider
        self._recorder = recorder

    def with_recorder(self, recorder: UsageRecorder) -> "LLMClient":
        """El mismo proveedor con otro registro de uso (las simulaciones guardan el uso aparte y
        lo escriben después de revertir lo simulado, decisión 080)."""
        return LLMClient(provider=self._provider, recorder=recorder)

    def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        case_id: uuid.UUID | None = None,
        role: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        """`role`: el papel de la llamada en los pasos del turno cuando el modelo pedido no
        alcanza para nombrarla (el supervisor usa el modelo del clasificador, decisión 079)."""
        if model not in PRICES:  # sin precio no hay costo: no se llama
            raise ValueError(f"Modelo sin precio configurado: {model}")
        with timing.step(timing.MODEL, "modelo") as span:
            if role:
                span["llm.role"] = role
            response = self._provider.create(
                model=model, messages=messages, max_tokens=max_tokens, system=system, **options
            )
            cost = cost_usd(response.model, response.usage)
            span.update(
                {
                    "llm.requested_model": model,
                    "llm.model": response.model,
                    "llm.input_tokens": response.usage.input_tokens,
                    "llm.output_tokens": response.usage.output_tokens,
                    "llm.cost_usd": float(cost),
                    "llm.stop_reason": str(response.stop_reason),
                    "llm.events": ",".join(response.events),
                }
            )
        # Se registra el modelo que respondió: con Gemini no es el que pidió el agente. Los
        # eventos (respaldo, llamada recuperada, rechazo) van solo si los hubo.
        extra: dict[str, Any] = {"events": list(response.events)} if response.events else {}
        self._recorder.record(
            tenant_id=tenant_id,
            case_id=case_id,
            kind="llm",
            model=response.model,
            usage=response.usage,
            cost_usd=cost,
            **extra,
        )
        return response
