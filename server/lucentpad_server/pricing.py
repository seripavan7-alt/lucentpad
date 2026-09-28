"""Per-model token prices used to compute ``cost_usd``.

Standard (non-batch, global) list prices in USD per million tokens, checked on 2026-09-25 at:
- Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI: https://developers.openai.com/api/docs/pricing

Cache prices are included: spans report cached input as subsets of the input tokens
(``gen_ai.usage.cache_read.input_tokens`` / ``cache_creation.input_tokens``). Re-check the pages
before quoting these numbers.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Price(NamedTuple):
    input_per_mtok: float  # USD per million uncached input tokens
    output_per_mtok: float  # USD per million output tokens
    cache_read_per_mtok: float  # cache hits
    cache_write_per_mtok: float  # 5-minute cache writes (Anthropic); OpenAI doesn't charge them


PRICES_CHECKED = "2026-09-25"

PRICES: dict[str, Price] = {
    # Anthropic: cache hits 0.1x input (0.05x on Opus 5.5), 5-minute writes 1.25x
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10, 1.25),
    # OpenAI: cached input 0.1x
    "gpt-5": Price(1.25, 10.00, 0.125, 1.25),
    "gpt-5-mini": Price(0.25, 2.00, 0.025, 0.25),
}

# Dated snapshots ("gpt-5-2026-08-07", "claude-haiku-4-5-20251001") are priced as their base model.
_DATED = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


def price_for(model: str) -> Price | None:
    """The price of ``model``, or of its base model when it names a dated snapshot."""
    return PRICES.get(model) or PRICES.get(_DATED.sub("", model))


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Cost of one call in USD, rounded to 8 decimals; ``None`` if the model is unpriced.

    ``input_tokens`` counts all input (OpenTelemetry's convention); the cached parts are priced at
    their own rates and the rest at the input rate.
    """
    price = price_for(model)
    if price is None:
        return None
    uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    total = (
        uncached * price.input_per_mtok
        + cache_read_tokens * price.cache_read_per_mtok
        + cache_write_tokens * price.cache_write_per_mtok
        + output_tokens * price.output_per_mtok
    )
    return round(total / 1_000_000, 8)
