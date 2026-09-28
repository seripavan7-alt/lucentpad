"""Exceptions the SDK raises on purpose (everything else it swallows)."""

from __future__ import annotations


class GuardrailBlocked(Exception):
    """A LucentPad guardrail rule blocked a model call or a tool call before it ran.

    ``rule`` is the rule id, ``reason`` says why (e.g. ``"amount 489 > 200: Refunds over $200
    need a human to approve them."``). The block is recorded as a ``kind="guardrail"`` span.
    """

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(f"blocked by guardrail {rule}: {reason}")
        self.rule = rule
        self.reason = reason


class BudgetExceeded(Exception):
    """The run's budget (``trace(..., budget_usd=..., on_budget="stop")``) was exceeded by an
    earlier model call, so this call was not sent to the provider."""

    def __init__(self, limit_usd: float, spent_usd: float) -> None:
        super().__init__(f"run budget of ${limit_usd:g} exceeded (spent ~${spent_usd:.6f})")
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd
