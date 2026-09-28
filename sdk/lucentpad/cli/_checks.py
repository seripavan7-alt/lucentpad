"""Deterministic checks (D24) over what one eval case did: its answer and its traced spans."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .._attrs import Attr
from ._suite import Check, ToolSpec


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Mapping[str, Any] | None  # from the tool span's input preview (JSON), when recorded
    status: str


@dataclass(frozen=True)
class Observation:
    answer: str
    tools: tuple[ToolCall, ...]
    cost_usd: float | None
    latency_ms: float
    error: str | None = None  # the target raised


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    detail: str | None = None


def tool_calls(spans: Iterable[Mapping[str, Any]]) -> tuple[ToolCall, ...]:
    """The ``kind="tool"`` spans of a trace, in start order. Blocked calls never ran, so they
    are ``kind="guardrail"`` spans and don't count."""
    tools = sorted((s for s in spans if s.get("kind") == "tool"), key=lambda s: s["start_time"])
    out: list[ToolCall] = []
    for s in tools:
        attrs = s.get("attributes") or {}
        args: Mapping[str, Any] | None = None
        preview = attrs.get(Attr.INPUT_PREVIEW)
        if isinstance(preview, str) and not attrs.get(Attr.INPUT_TRUNCATED):
            try:
                parsed = json.loads(preview)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        out.append(ToolCall(str(s.get("name")), args, str(s.get("status"))))
    return tuple(out)


def _same(expected: Any, actual: Any) -> bool:
    if expected == actual:
        return True
    scalars = (str, int, float)
    return (
        isinstance(expected, scalars)
        and isinstance(actual, scalars)
        and not isinstance(expected, bool)
        and not isinstance(actual, bool)
        and str(expected) == str(actual)  # "1042" vs 1042
    )


def _args_match(expected: Mapping[str, Any], actual: Mapping[str, Any] | None) -> bool:
    return actual is not None and all(
        k in actual and _same(v, actual[k]) for k, v in expected.items()
    )


def _names(tools: Iterable[ToolCall]) -> str:
    return ", ".join(dict.fromkeys(t.name for t in tools))


def evaluate(check: Check, obs: Observation) -> CheckResult:
    kind, value, label = check.kind, check.value, check.label
    answer = obs.answer
    if kind == "contains":
        ok = str(value).lower() in answer.lower()
        return CheckResult(label, ok, None if ok else "not in the answer")
    if kind == "not_contains":
        ok = str(value).lower() not in answer.lower()
        return CheckResult(label, ok, None if ok else "found in the answer")
    if kind == "regex":
        ok = re.search(str(value), answer) is not None
        return CheckResult(label, ok, None if ok else "no match in the answer")
    if kind == "tool_called":
        assert isinstance(value, ToolSpec)
        calls = [t for t in obs.tools if t.name == value.name]
        if not calls:
            called = _names(obs.tools)
            detail = f"not called; tools called: {called}" if obs.tools else "no tool was called"
            return CheckResult(label, False, detail)
        if value.args and not any(_args_match(value.args, t.args) for t in calls):
            seen = [t.args for t in calls if t.args is not None]
            detail = (
                f"{value.name} called with {json.dumps(seen[-1], sort_keys=True)[:200]}"
                if seen
                else f"{value.name} called, but its arguments weren't recorded"
            )
            return CheckResult(label, False, detail)
        return CheckResult(label, True)
    if kind == "no_tool":
        calls = [t for t in obs.tools if value is None or t.name == value]
        if calls:
            return CheckResult(label, False, f"called: {_names(calls)}")
        return CheckResult(label, True)
    limit = float(value) if isinstance(value, int | float) else 0.0
    if kind == "max_cost_usd":
        if obs.cost_usd is None:
            return CheckResult(label, False, "cost unknown")
        ok = obs.cost_usd <= limit
        return CheckResult(label, ok, None if ok else f"cost ${obs.cost_usd:.6f} > ${limit:g}")
    ok = obs.latency_ms <= limit
    return CheckResult(label, ok, None if ok else f"latency {obs.latency_ms:.0f} ms > {limit:g} ms")


def evaluate_all(checks: Iterable[Check], obs: Observation) -> list[CheckResult]:
    if obs.error is not None:
        return [CheckResult("run", False, obs.error[:500])]
    return [evaluate(c, obs) for c in checks]
