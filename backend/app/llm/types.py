import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

UsageKind = Literal["llm", "embedding"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    stop_reason: str | None
    usage: TokenUsage
    # Bloques de la respuesta tal como hay que devolverlos en el turno siguiente.
    content: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None  # respuesta original del proveedor
    # Lo que pasó al conseguir la respuesta, para las métricas: "respaldo" (respondió el
    # proveedor de respaldo), "recuperada" (llamada con el nombre roto, reparada) o "rechazo".
    events: tuple[str, ...] = ()


class UsageRecorder(Protocol):
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
    ) -> None: ...
