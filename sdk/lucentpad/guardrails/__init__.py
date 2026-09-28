"""Guardrail engine: redaction and rule matching. Pure Python, no I/O, no network.

Shared by the SDK (before export and before calls), the gateway (before recording and before
forwarding) and the server's ingest (last line), so all three apply exactly the same logic.
The server imports this package; this package must never import the server.

Public API:

    redact(text) -> Redacted                 # replace API keys, emails, card numbers
    parse_rules(data) -> list[Rule]          # from the YAML/JSON rules document
    check_prompt(rules, text) -> Block|None  # prompt rules on the user's message
    check_tool(rules, tool, args) -> Block|None  # tool rules on a tool call's arguments
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

from ._condition import evaluate, parse_condition
from ._redact import redact
from ._types import Block, Redacted, RedactionKind, Rule

__all__ = [
    "Block",
    "Redacted",
    "RedactionKind",
    "Rule",
    "check_prompt",
    "check_tool",
    "parse_rules",
    "redact",
]

_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")  # same as schema.GuardrailRule.id
_RULE_ID_MAX = 100


# --------------------------------------------------------------------------- parse_rules


def _opt_str(rule_id: str, raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"rule {rule_id!r}: {key} must be a string")
    return value


def _parse_rule(raw: Any, index: int) -> Rule:
    if not isinstance(raw, Mapping):
        raise ValueError(f"rule #{index + 1}: must be a mapping")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise ValueError(f"rule #{index + 1}: missing id")
    if len(rule_id) > _RULE_ID_MAX or not _RULE_ID.match(rule_id):
        raise ValueError(
            f"rule {rule_id!r}: id must be lowercase letters, digits, '_', '.' or '-' "
            f"(at most {_RULE_ID_MAX} characters)"
        )
    rtype = raw.get("type")
    if rtype not in ("prompt", "tool"):
        raise ValueError(f"rule {rule_id!r}: type must be 'prompt' or 'tool'")
    message = raw.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError(f"rule {rule_id!r}: missing message")
    description = _opt_str(rule_id, raw, "description")
    pattern = _opt_str(rule_id, raw, "pattern")
    tool = _opt_str(rule_id, raw, "tool")
    condition = _opt_str(rule_id, raw, "condition")
    kw_raw = raw.get("keywords") or []
    if isinstance(kw_raw, str) or not isinstance(kw_raw, Sequence):
        raise ValueError(f"rule {rule_id!r}: keywords must be a list of strings")
    keywords: list[str] = []
    for kw in kw_raw:
        if not isinstance(kw, str) or not kw.strip():
            raise ValueError(f"rule {rule_id!r}: keywords must be non-empty strings")
        keywords.append(kw.strip())

    if rtype == "prompt":
        if not keywords and not pattern:
            raise ValueError(f"rule {rule_id!r}: a prompt rule needs keywords or a pattern")
        if tool is not None or condition is not None:
            raise ValueError(f"rule {rule_id!r}: tool/condition are for tool rules")
        if pattern is not None:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"rule {rule_id!r}: invalid pattern: {exc}") from None
    else:
        if not tool or not tool.strip():
            raise ValueError(f"rule {rule_id!r}: a tool rule needs a tool name")
        if keywords or pattern is not None:
            raise ValueError(f"rule {rule_id!r}: keywords/pattern are for prompt rules")
        if condition is not None:
            try:
                parse_condition(condition)
            except ValueError as exc:
                raise ValueError(f"rule {rule_id!r}: invalid condition: {exc}") from None
    return Rule(
        id=rule_id,
        type=rtype,
        message=message.strip(),
        description=description,
        keywords=tuple(keywords),
        pattern=pattern,
        tool=tool.strip() if tool else None,
        condition=condition,
    )


def parse_rules(data: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Rule]:
    """Rules from a parsed rules document (``{"rules": [...]}`` or a bare list). Raises
    ``ValueError`` naming the bad rule on invalid input (bad regex, unparsable condition).

    Other top-level keys (``source``, ``version``) and unknown rule keys are ignored. A tool rule
    without a ``condition`` blocks every call of that tool."""
    if isinstance(data, Mapping):
        items = data.get("rules")
        if items is None:
            return []
    else:
        items = data
    if isinstance(items, str | bytes) or not isinstance(items, Sequence):
        raise ValueError("rules must be a list")
    rules = [_parse_rule(raw, i) for i, raw in enumerate(items)]
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise ValueError(f"rule {rule.id!r}: duplicate id")
        seen.add(rule.id)
    return rules


# --------------------------------------------------------------------------- check_prompt


@lru_cache(maxsize=256)
def _keyword_regex(keywords: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=256)
def _pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def check_prompt(rules: Sequence[Rule], text: str) -> Block | None:
    """The first prompt rule matching ``text`` (a keyword, case-insensitive whole word, or the
    regex), else None."""
    if not text:
        return None
    for rule in rules:
        if rule.type != "prompt":
            continue
        if rule.keywords:
            m = _keyword_regex(rule.keywords).search(text)
            if m is not None:
                said = m.group(0).casefold()
                kw = next((k for k in rule.keywords if k.casefold() == said), m.group(0))
                return Block(rule.id, f'keyword "{kw}": {rule.message}')
        if rule.pattern and _pattern(rule.pattern).search(text):
            return Block(rule.id, f"pattern matched: {rule.message}")
    return None


# --------------------------------------------------------------------------- check_tool


def check_tool(rules: Sequence[Rule], tool: str, args: Mapping[str, Any]) -> Block | None:
    """The first tool rule for ``tool`` whose condition holds on ``args``, else None.
    Conditions: ``<field> <op> <literal>`` joined by ``and``/``or`` (parentheses allowed); ops
    ``> >= < <= == !=``, ``in``/``not in`` a list; literals numbers, quoted strings,
    true/false/null; fields may be nested (``customer.country``). No code is run.

    The block reason names what matched, e.g. ``amount 489 > 200: <rule message>``."""
    for rule in rules:
        if rule.type != "tool" or rule.tool != tool:
            continue
        if rule.condition is None:
            return Block(rule.id, rule.message)
        matched = evaluate(parse_condition(rule.condition), args, tool)
        if matched is not None:
            return Block(rule.id, f"{' and '.join(matched)}: {rule.message}")
    return None
