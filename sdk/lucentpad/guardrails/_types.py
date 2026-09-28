"""Value types of the guardrail engine (re-exported by ``lucentpad.guardrails``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

RedactionKind = Literal["email", "api_key", "card"]


@dataclass(frozen=True)
class Redacted:
    """``text`` with every detected value replaced by ``[REDACTED:<kind>]``; ``counts`` says how
    many of each kind were replaced (never the values)."""

    text: str
    counts: Mapping[RedactionKind, int] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.counts)


@dataclass(frozen=True)
class Rule:
    """One blocking rule; same fields as ``schema.GuardrailRule`` on the server."""

    id: str
    type: Literal["prompt", "tool"]
    message: str
    description: str | None = None
    keywords: tuple[str, ...] = ()
    pattern: str | None = None
    tool: str | None = None
    condition: str | None = None


@dataclass(frozen=True)
class Block:
    """A rule matched: the call must not go ahead."""

    rule: str  # rule id
    reason: str  # human-readable, e.g. "refund of $489 exceeds the $200 limit" or rule.message
