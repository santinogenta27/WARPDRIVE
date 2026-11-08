import uuid
from dataclasses import dataclass
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
class LLMResponse:
    text: str
    model: str
    stop_reason: str | None
    usage: TokenUsage
    raw: Any = None  # respuesta original del proveedor (bloques de herramientas, etc.)


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
    ) -> None: ...
