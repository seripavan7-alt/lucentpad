"""Eval suites: the YAML file ``lucentpad eval`` runs.

suite: support_agent                 # name shown on the Evals page (default: file stem)
target: support_agent.evals:run_case # module:function, called as fn(input, model=, mock=)
model: claude-haiku-4-5              # passed to the target (optional; --model overrides)
service: support-agent               # service.name on the eval's spans (optional)
rules: rules.yaml                    # guardrail rules for --mock / API down (optional; a
                                     # path relative to this file, or inline)
cases:
  - id: order_status
    input: "Where is order 1043?"
    checks:
      - tool_called: lookup_order                                # any call to the tool
      - tool_called: {name: issue_refund, args: {amount: 77}}    # + argument subset
      - no_tool: issue_refund                                    # never called
      - no_tool                                                  # no tool at all
      - contains: "1043"             # case-insensitive substring of the answer
      - not_contains: refunded
      - regex: "(?i)shipped|delivered"
      - max_cost_usd: 0.02
      - max_latency_ms: 20000
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from ..guardrails import parse_rules

CheckKind = Literal[
    "contains", "not_contains", "regex", "tool_called", "no_tool", "max_cost_usd", "max_latency_ms"
]
CHECK_KINDS: tuple[CheckKind, ...] = (
    "contains",
    "not_contains",
    "regex",
    "tool_called",
    "no_tool",
    "max_cost_usd",
    "max_latency_ms",
)


class SuiteError(ValueError):
    """The suite file is invalid (the message says where)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    value: str | float | ToolSpec | None
    label: str  # e.g. "contains: colleague" (EvalCheckResult.check)


@dataclass(frozen=True)
class Case:
    id: str
    input: str
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Suite:
    name: str
    path: Path
    target: str
    model: str | None
    service: str | None
    rules: Any | None  # a parsed rules document (validated), or None
    cases: tuple[Case, ...]


def _number(where: str, kind: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SuiteError(f"{where}: {kind} must be a number")
    if value < 0:
        raise SuiteError(f"{where}: {kind} must not be negative")
    return float(value)


def _text(where: str, kind: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise SuiteError(f"{where}: {kind} must be a non-empty string")
    return value


def _tool_spec(where: str, value: Any) -> ToolSpec:
    if isinstance(value, str) and value:
        return ToolSpec(value)
    if isinstance(value, Mapping):
        unknown = set(value) - {"name", "args"}
        if unknown:
            raise SuiteError(f"{where}: tool_called has unknown keys {sorted(unknown)}")
        name = _text(where, "tool_called.name", value.get("name"))
        args = value.get("args")
        if args is not None and not isinstance(args, Mapping):
            raise SuiteError(f"{where}: tool_called.args must be a mapping")
        return ToolSpec(name, dict(args) if args is not None else None)
    raise SuiteError(f"{where}: tool_called must be a tool name or {{name, args}}")


def _fmt(value: float) -> str:
    return f"{value:g}"


def parse_check(where: str, raw: Any) -> Check:
    if raw == "no_tool":
        return Check("no_tool", None, "no_tool")
    if not isinstance(raw, Mapping) or len(raw) != 1:
        raise SuiteError(f"{where}: a check is a one-key mapping, e.g. `contains: refund`")
    ((kind, value),) = raw.items()
    if kind not in CHECK_KINDS:
        raise SuiteError(f"{where}: unknown check {kind!r} (one of {', '.join(CHECK_KINDS)})")
    if kind in ("contains", "not_contains"):
        text = _text(where, kind, value if not isinstance(value, int | float) else str(value))
        return Check(kind, text, f"{kind}: {text}")
    if kind == "regex":
        pattern = _text(where, kind, value)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SuiteError(f"{where}: invalid regex {pattern!r}: {exc}") from None
        return Check("regex", pattern, f"regex: {pattern}")
    if kind == "tool_called":
        spec = _tool_spec(where, value)
        label = f"tool_called: {spec.name}"
        if spec.args:
            label += " " + json.dumps(spec.args, sort_keys=True)
        return Check("tool_called", spec, label)
    if kind == "no_tool":
        if value is None or value is True:
            return Check("no_tool", None, "no_tool")
        name = _text(where, kind, value)
        return Check("no_tool", name, f"no_tool: {name}")
    limit = _number(where, kind, value)
    return Check(kind, limit, f"{kind}: {_fmt(limit)}")


def _load_rules(path: Path, raw: Any) -> Any:
    if isinstance(raw, str):
        file = (path.parent / raw).resolve()
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise SuiteError(f"rules: cannot read {file}: {exc}") from None
    try:
        parse_rules(raw)
    except ValueError as exc:
        raise SuiteError(f"rules: {exc}") from None
    return raw


def load_suite(path: str | Path) -> Suite:
    """Read and validate a suite file. Raises ``SuiteError``."""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SuiteError(f"cannot read {p}: {exc.strerror or exc}") from None
    except yaml.YAMLError as exc:
        raise SuiteError(f"{p}: invalid YAML: {exc}") from None
    if not isinstance(data, Mapping):
        raise SuiteError(f"{p}: expected a mapping with target and cases")
    known = {"suite", "description", "target", "model", "service", "rules", "cases"}
    unknown = set(data) - known
    if unknown:
        raise SuiteError(f"{p}: unknown keys {sorted(unknown)}")
    name = data.get("suite", p.stem)
    if not isinstance(name, str) or not name or len(name) > 200:
        raise SuiteError(f"{p}: suite must be a name of 1-200 characters")
    target = data.get("target")
    if not isinstance(target, str) or not re.fullmatch(r"[\w.]+:[\w.]+", target):
        raise SuiteError(f"{p}: target must be `module:function`")
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        raise SuiteError(f"{p}: model must be a string")
    service = data.get("service")
    if service is not None and (not isinstance(service, str) or not service):
        raise SuiteError(f"{p}: service must be a string")
    rules = _load_rules(p, data["rules"]) if data.get("rules") is not None else None
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, str) or not raw_cases:
        raise SuiteError(f"{p}: cases must be a non-empty list")
    cases: list[Case] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_cases):
        where = f"case #{i + 1}"
        if not isinstance(raw, Mapping):
            raise SuiteError(f"{where}: must be a mapping with id, input and checks")
        unknown = set(raw) - {"id", "input", "checks"}
        if unknown:
            raise SuiteError(f"{where}: unknown keys {sorted(unknown)}")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[\w.-]{1,200}", case_id):
            raise SuiteError(f"{where}: id must be letters, digits, '_', '.' or '-'")
        where = f"case {case_id!r}"
        if case_id in seen:
            raise SuiteError(f"{where}: duplicate id")
        seen.add(case_id)
        text = raw.get("input")
        if not isinstance(text, str) or not text.strip():
            raise SuiteError(f"{where}: input must be a non-empty string")
        raw_checks = raw.get("checks")
        if not isinstance(raw_checks, Sequence) or isinstance(raw_checks, str) or not raw_checks:
            raise SuiteError(f"{where}: checks must be a non-empty list")
        checks = tuple(parse_check(f"{where}, check #{j + 1}", c) for j, c in enumerate(raw_checks))
        cases.append(Case(case_id, text, checks))
    return Suite(name, p, target, model, service, rules, tuple(cases))
