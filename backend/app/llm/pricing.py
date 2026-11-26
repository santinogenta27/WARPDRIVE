"""Precios en USD por millón de tokens (decisiones 003 y 102).

Los precios viven en una tabla de configuración, `prices.csv`: modelo, entrada, salida, escritura y
lectura de caché, desde cuándo rige (`since`, vacío: siempre), fuente y fecha en que se verificó.
Este módulo solo la lee. Para cambiar un precio se agrega una fila con su `since`: el costo de cada
llamada usa la tarifa que rige ese día.

Con key propia (decisión 102) el uso se registra como `byok/<proveedor>/<modelo>`, con el precio de
lista del proveedor; los modelos de la plataforma en nivel gratuito (Gemini, Groq) cuestan 0.
"""

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from app.llm.types import TokenUsage

_MILLION = Decimal(1_000_000)
TABLE_PATH = Path(__file__).with_name("prices.csv")


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


@dataclass(frozen=True)
class PriceRow:
    model: str
    price: Price
    since: date | None
    source: str
    checked: date


def _load(path: Path = TABLE_PATH) -> list[PriceRow]:
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                PriceRow(
                    model=raw["model"].strip(),
                    price=Price(
                        Decimal(raw["input"]),
                        Decimal(raw["output"]),
                        Decimal(raw["cache_write"]),
                        Decimal(raw["cache_read"]),
                    ),
                    since=date.fromisoformat(raw["since"]) if raw["since"].strip() else None,
                    source=raw["source"].strip(),
                    checked=date.fromisoformat(raw["checked"]),
                )
            )
    return rows


TABLE: list[PriceRow] = _load()


def _today() -> date:
    return datetime.now(UTC).date()


def price_for(model: str, on: date | None = None) -> Price | None:
    """La tarifa que rige `on` (hoy si no se pasa), o None si el modelo no tiene precio."""
    day = on or _today()
    found = [r for r in TABLE if r.model == model and (r.since is None or r.since <= day)]
    if not found:
        return None
    return max(found, key=lambda r: r.since or date.min).price


def known(model: str) -> bool:
    return any(r.model == model for r in TABLE)


# La tarifa vigente de cada modelo: para saber si un modelo tiene precio antes de llamarlo.
PRICES: dict[str, Price] = {
    model: price for model in {r.model for r in TABLE} if (price := price_for(model)) is not None
}


def last_checked() -> date:
    return max(r.checked for r in TABLE)


def cost_usd(model: str, usage: TokenUsage, on: date | None = None) -> Decimal:
    price = price_for(model, on)
    if price is None:
        raise ValueError(f"Modelo sin precio configurado: {model}")
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / _MILLION
    return total.normalize()
