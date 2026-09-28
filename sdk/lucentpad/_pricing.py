"""Spend estimates for budgets, from the server's price table (``GET /v1/pricing``).

Same formula as ``lucentpad_server.pricing.cost_usd`` (a test checks they agree): input tokens
count all input; cached reads/writes are priced at their own rates and the rest at the input
rate; dated snapshots (``claude-haiku-4-5-20251001``, ``gpt-5-2026-08-07``) fall back to their
base model.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, NamedTuple


class Price(NamedTuple):
    input: float  # USD per million uncached input tokens
    output: float
    cache_read: float
    cache_write: float


PriceTable = Mapping[str, Price]

_DATED = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{8})$")


def parse_price_table(body: Any) -> dict[str, Price]:
    """``{"prices": [{"model", "input", "output", "cache_read", "cache_write"}], ...}`` ->
    ``{model: Price}``. Raises ``ValueError`` on a malformed body."""
    if not isinstance(body, Mapping) or not isinstance(body.get("prices"), list):
        raise ValueError("price table: missing prices")
    table: dict[str, Price] = {}
    for item in body["prices"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("model"), str):
            raise ValueError("price table: bad entry")
        values = []
        for key in ("input", "output", "cache_read", "cache_write"):
            v = item.get(key)
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                raise ValueError(f"price table: bad {key} for {item['model']}")
            values.append(float(v))
        table[item["model"]] = Price(*values)
    return table


def price_for(table: PriceTable, model: str) -> Price | None:
    return table.get(model) or table.get(_DATED.sub("", model))


def cost_usd(
    table: PriceTable,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Estimated cost of one call in USD (8 decimals); None when the model is unpriced."""
    price = price_for(table, model)
    if price is None:
        return None
    uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    total = (
        uncached * price.input
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.cache_write
        + output_tokens * price.output
    )
    return round(total / 1_000_000, 8)
