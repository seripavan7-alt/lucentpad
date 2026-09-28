"""Demo configuration (decisions D1, D2 in docs/STATUS.md)."""

from __future__ import annotations

from typing import Final

SERVICE_NAME: Final = "support-agent"
RUN_NAME: Final = "support-agent.run"
MODEL: Final = "claude-sonnet-5"
FALLBACK_MODEL: Final = "claude-haiku-4-5"  # used by failover in M3; not in M1
MAX_TOKENS: Final = 1024
MAX_TURNS: Final = 8

# D1. The refund limit is enforced by a LucentPad guardrail (the server's built-in
# `refund_limit` rule, mirrored in rules.yaml for offline runs), not by the prompt, so the demo
# can show the block. The budget is the run's `trace(budget_usd=...)` (alert mode, D23).
REFUND_LIMIT_USD: Final = 200.0
BUDGET_USD_PER_RUN: Final = 0.50

# Same value as lucentpad_server.schema.Attr.REFUND_AMOUNT (a test checks it); the agent does
# not import the server package.
ATTR_REFUND_AMOUNT: Final = "lucentpad.refund.amount"
ATTR_INPUT_PREVIEW: Final = "lucentpad.input.preview"  # tool arguments (JSON), redacted by the SDK
ATTR_OUTPUT_PREVIEW: Final = "lucentpad.output.preview"  # tool result (JSON)

DEFAULT_QUESTION: Final = "Where's order 1042? I want a refund."

# Keep "Always look an order up" on its own line: --mock-llm follows it (and the demo-break
# patch in evals/ removes it, so the eval gate can be shown failing).
SYSTEM_PROMPT: Final = (
    "You are the support agent for Lucent Coffee Gear, an online store. Be brief and friendly. "
    "Always look an order up before answering questions about it. "
    "If the customer asks for a refund on a delivered order, refund the order total with "
    "issue_refund, then draft a short confirmation email to the customer's address with "
    "draft_email. If they want a different delivery time for an order that hasn't shipped yet, "
    "move it with reschedule_delivery. Finish with a short reply to the customer."
)

# What the customer hears when a LucentPad guardrail blocks a call (D22): the run hands off
# politely instead of failing.
HANDOFF_REFUND: Final = (
    "Refunds of this size need a colleague's approval, so I've passed your request on. "
    "They'll approve it within one business day, and you'll get an email when it's done."
)
HANDOFF_OTHER: Final = (
    "I can't help with that here, so I've passed it to a colleague, who will get back to you "
    "within one business day."
)
