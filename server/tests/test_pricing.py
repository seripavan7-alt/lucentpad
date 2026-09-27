"""Price table rules: dated snapshots, cached input."""

from __future__ import annotations

import pytest

from lucentpad_server.pricing import cost_usd, price_for


def test_dated_snapshots_use_the_base_price() -> None:
    assert price_for("gpt-5-2026-08-07") == price_for("gpt-5")
    assert price_for("claude-haiku-4-5-20251001") == price_for("claude-haiku-4-5")
    assert price_for("gpt-5-turbo") is None
    assert cost_usd("mystery-model", 10, 10) is None


def test_cached_input_is_priced_at_cache_rates() -> None:
    # claude-sonnet-5: $2 in, $10 out, $0.20 cache read, $2.50 5-minute write (per MTok)
    plain = cost_usd("claude-sonnet-5", 1_000_000, 0)
    assert plain == pytest.approx(2.0)
    # 1M input of which 800k read from cache and 100k written: 100k*2 + 800k*0.2 + 100k*2.5
    mixed = cost_usd("claude-sonnet-5", 1_000_000, 0, 800_000, 100_000)
    assert mixed == pytest.approx(0.2 + 0.16 + 0.25)
    # OpenAI cached input (cached tokens are part of prompt_tokens)
    assert cost_usd("gpt-5", 1_000_000, 0, 1_000_000) == pytest.approx(0.125)
