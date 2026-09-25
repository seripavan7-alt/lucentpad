"""Per-model token prices used to compute ``cost_usd``.

Standard (non-batch, global) list prices in USD per million tokens, checked on 2026-09-25 at:
- Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI: https://developers.openai.com/api/docs/pricing

Cache-read and cache-write prices exist too, but spans don't carry cache token counts yet, so
only base input and output are priced. Re-check the pages before quoting these numbers.
"""

from __future__ import annotations

from typing import NamedTuple


class Price(NamedTuple):
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens


PRICES: dict[str, Price] = {
    # Anthropic
    "claude-opus-5-5": Price(4.00, 20.00),
    "claude-sonnet-5": Price(2.00, 10.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
    # OpenAI
    "gpt-5": Price(1.25, 10.00),
    "gpt-5-mini": Price(0.25, 2.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost of one call in USD, rounded to 8 decimals; ``None`` if the model is unpriced."""
    price = PRICES.get(model)
    if price is None:
        return None
    total = input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
    return round(total / 1_000_000, 8)
