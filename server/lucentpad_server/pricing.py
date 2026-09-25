"""Per-model token prices used to compute ``cost_usd``.

ILLUSTRATIVE PLACEHOLDERS ONLY. These models and prices are stand-ins so the sample data
and dashboard have plausible numbers. They are NOT checked against any provider's price
list and must not be quoted as accurate. Choosing the real demo models, their fallbacks and
their prices is an open M1 decision (see docs/PRD.md, "Decisions and open questions").
"""

from __future__ import annotations

from typing import NamedTuple


class Price(NamedTuple):
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens


PRICES: dict[str, Price] = {
    # Anthropic (illustrative)
    "claude-opus-5-5": Price(5.00, 25.00),
    "claude-sonnet-5": Price(3.00, 15.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
    # OpenAI (illustrative)
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
