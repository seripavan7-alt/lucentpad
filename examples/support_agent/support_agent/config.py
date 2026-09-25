"""Demo configuration (decisions D1, D2 in docs/STATUS.md)."""

from __future__ import annotations

from typing import Final

SERVICE_NAME: Final = "support-agent"
RUN_NAME: Final = "support-agent.run"
MODEL: Final = "claude-sonnet-5"
FALLBACK_MODEL: Final = "claude-haiku-4-5"  # used by failover in M3; not in M1
MAX_TOKENS: Final = 1024
MAX_TURNS: Final = 8

# D1: config only in M1. The refund limit is enforced by a LucentPad guardrail in M3 (not by
# the prompt, so the demo can show the block), and the budget alert arrives in M3 too.
REFUND_LIMIT_USD: Final = 200.0
BUDGET_USD_PER_RUN: Final = 0.50

# Same value as lucentpad_server.schema.Attr.REFUND_AMOUNT (a test checks it); the agent does
# not import the server package.
ATTR_REFUND_AMOUNT: Final = "lucentpad.refund.amount"

DEFAULT_QUESTION: Final = "Where's order 1042? I want a refund."

SYSTEM_PROMPT: Final = (
    "You are the support agent for Lucent Coffee Gear, an online store. Be brief and friendly. "
    "Always look an order up before answering questions about it. If the customer asks for a "
    "refund on a delivered order, refund the order total with issue_refund, then draft a short "
    "confirmation email to the customer's address with draft_email. Finish with a short "
    "reply to the customer."
)
