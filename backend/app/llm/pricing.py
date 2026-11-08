"""Precios en USD por millón de tokens. Ver docs/decisions/003 para la fuente de cada valor."""

from dataclasses import dataclass
from decimal import Decimal

from app.llm.types import TokenUsage

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


def _d(value: str) -> Decimal:
    return Decimal(value)


PRICES: dict[str, Price] = {
    # Claude (API de Anthropic, tarifas de primera parte)
    "claude-opus-5-5": Price(_d("4"), _d("20"), _d("5"), _d("0.20")),
    "claude-sonnet-5-5": Price(_d("2"), _d("10"), _d("2.5"), _d("0.20")),
    "claude-haiku-5-5": Price(_d("0.10"), _d("0.50"), _d("0.125"), _d("0.01")),
    # Embeddings (Voyage AI): solo tokens de entrada
    "voyage-multilingual-2": Price(_d("0.12"), _d("0"), _d("0"), _d("0")),
}


def cost_usd(model: str, usage: TokenUsage) -> Decimal:
    price = PRICES.get(model)
    if price is None:
        raise ValueError(f"Modelo sin precio configurado: {model}")
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / _MILLION
    return total.normalize()
