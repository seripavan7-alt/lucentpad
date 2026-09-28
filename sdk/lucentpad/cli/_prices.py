"""Cost estimates for eval cases that can't be read back from the LucentPad API.

The API computes cost server-side and ``lucentpad eval`` prefers it. Without the API (``--mock``,
or CI without a LucentPad server) cost is estimated from the spans' token counts with the price
table the SDK fetched, else with this copy of the server's list prices (``lucentpad_server.
pricing.PRICES``; a test keeps them equal).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .._attrs import Attr
from .._pricing import Price, PriceTable, cost_usd

FALLBACK_PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10, 1.25),
    "gpt-5": Price(1.25, 10.00, 0.125, 1.25),
    "gpt-5-mini": Price(0.25, 2.00, 0.025, 0.25),
}


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def spans_cost(spans: Iterable[Mapping[str, Any]], table: PriceTable) -> float | None:
    """Sum of the llm spans' costs; None if any llm span's model is unpriced."""
    total = 0.0
    for s in spans:
        if s.get("kind") != "llm":
            continue
        a = s.get("attributes") or {}
        cost = None
        for key in (Attr.GEN_AI_RESPONSE_MODEL, Attr.GEN_AI_REQUEST_MODEL):
            model = a.get(key)
            if isinstance(model, str) and model:
                cost = cost_usd(
                    table,
                    model,
                    _int(a.get(Attr.GEN_AI_INPUT_TOKENS)),
                    _int(a.get(Attr.GEN_AI_OUTPUT_TOKENS)),
                    _int(a.get(Attr.GEN_AI_CACHE_READ_TOKENS)),
                    _int(a.get(Attr.GEN_AI_CACHE_CREATION_TOKENS)),
                )
                if cost is not None:
                    break
        if cost is None:
            return None
        total += cost
    return round(total, 8)
