"""Tool-rule conditions: a tiny hand-written tokenizer, parser and evaluator. No code is run.

Grammar (keywords are case-insensitive)::

    expr       := and_expr ("or" and_expr)*
    and_expr   := atom ("and" atom)*
    atom       := "(" expr ")" | comparison
    comparison := path op literal | path ["not"] "in" list
    path       := name ("." (name | index))*          e.g. amount, customer.country, items.0.sku
    op         := ">" | ">=" | "<" | "<=" | "==" | "!="
    literal    := number | "string" | 'string' | true | false | null
    list       := "[" [literal ("," literal)*] "]"

Semantics: a comparison on a missing field is false (so a rule never blocks on data it can't
see). Numbers compare with numbers or numeric strings (``"489.00"``); booleans are not numbers.
Strings compare with strings (ordering is lexicographic). Mismatched types: ``!=`` is true,
everything else false.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

__all__ = ["Condition", "evaluate", "parse_condition"]

MAX_CONDITION_CHARS = 2000
MAX_DEPTH = 32

Literal = float | int | str | bool | None


@dataclass(frozen=True)
class Compare:
    path: tuple[str, ...]
    op: str  # > >= < <= == != in "not in"
    value: Literal | tuple[Literal, ...]


@dataclass(frozen=True)
class BoolOp:
    op: str  # "and" | "or"
    items: tuple[Condition, ...]


Condition = Compare | BoolOp

# --------------------------------------------------------------------------- tokenizer

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|\d+))*)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<op>>=|<=|==|!=|>|<)
  | (?P<punct>[()\[\],])
    """,
    re.VERBOSE,
)
_KEYWORDS = {"and", "or", "in", "not", "true", "false", "null"}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}


@dataclass(frozen=True)
class _Tok:
    kind: str  # number | path | keyword | string | op | punct | end
    text: str
    pos: int


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tokenize(src: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if m is None:
            raise ValueError(f"unexpected character {src[pos]!r} at position {pos}")
        kind = m.lastgroup or ""
        text = m.group(0)
        if kind == "path" and text.lower() in _KEYWORDS:
            toks.append(_Tok("keyword", text.lower(), pos))
        elif kind != "ws":
            toks.append(_Tok(kind, text, pos))
        pos = m.end()
    toks.append(_Tok("end", "", pos))
    return toks


# --------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, src: str) -> None:
        self.toks = _tokenize(src)
        self.i = 0
        self.depth = 0

    def peek(self) -> _Tok:
        return self.toks[self.i]

    def take(self) -> _Tok:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def fail(self, what: str) -> ValueError:
        tok = self.peek()
        found = "end of condition" if tok.kind == "end" else repr(tok.text)
        return ValueError(f"expected {what} at position {tok.pos}, found {found}")

    def is_kw(self, word: str) -> bool:
        tok = self.peek()
        return tok.kind == "keyword" and tok.text == word

    def parse(self) -> Condition:
        node = self.expr()
        if self.peek().kind != "end":
            raise self.fail("'and', 'or' or the end")
        return node

    def expr(self) -> Condition:
        items = [self.and_expr()]
        while self.is_kw("or"):
            self.take()
            items.append(self.and_expr())
        return items[0] if len(items) == 1 else BoolOp("or", tuple(items))

    def and_expr(self) -> Condition:
        items = [self.atom()]
        while self.is_kw("and"):
            self.take()
            items.append(self.atom())
        return items[0] if len(items) == 1 else BoolOp("and", tuple(items))

    def atom(self) -> Condition:
        tok = self.peek()
        if tok.kind == "punct" and tok.text == "(":
            self.take()
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise ValueError("condition is nested too deeply")
            node = self.expr()
            if not (self.peek().kind == "punct" and self.peek().text == ")"):
                raise self.fail("')'")
            self.take()
            self.depth -= 1
            return node
        return self.comparison()

    def comparison(self) -> Compare:
        tok = self.peek()
        if tok.kind != "path":
            raise self.fail("a field name")
        self.take()
        path = tuple(tok.text.split("."))
        nxt = self.peek()
        if nxt.kind == "op":
            self.take()
            return Compare(path, nxt.text, self.literal())
        if self.is_kw("in"):
            self.take()
            return Compare(path, "in", self.list_literal())
        if self.is_kw("not"):
            self.take()
            if not self.is_kw("in"):
                raise self.fail("'in' after 'not'")
            self.take()
            return Compare(path, "not in", self.list_literal())
        raise self.fail("a comparison operator (> >= < <= == != in, not in)")

    def literal(self) -> Literal:
        tok = self.peek()
        if tok.kind == "number":
            self.take()
            text = tok.text
            if any(c in text for c in ".eE"):
                value = float(text)
                if not math.isfinite(value):
                    raise ValueError(f"number out of range at position {tok.pos}")
                return value
            return int(text)
        if tok.kind == "string":
            self.take()
            return _unquote(tok.text)
        if tok.kind == "keyword" and tok.text in ("true", "false", "null"):
            self.take()
            return {"true": True, "false": False, "null": None}[tok.text]
        raise self.fail("a number, a quoted string, true, false or null")

    def list_literal(self) -> tuple[Literal, ...]:
        tok = self.peek()
        if not (tok.kind == "punct" and tok.text == "["):
            raise self.fail("'['")
        self.take()
        items: list[Literal] = []
        if self.peek().kind == "punct" and self.peek().text == "]":
            self.take()
            return ()
        while True:
            items.append(self.literal())
            tok = self.peek()
            if tok.kind == "punct" and tok.text == ",":
                self.take()
                continue
            if tok.kind == "punct" and tok.text == "]":
                self.take()
                return tuple(items)
            raise self.fail("',' or ']'")


@lru_cache(maxsize=512)
def parse_condition(src: str) -> Condition:
    """Parse a condition; ``ValueError`` with a position on bad input."""
    if not isinstance(src, str) or not src.strip():
        raise ValueError("condition is empty")
    if len(src) > MAX_CONDITION_CHARS:
        raise ValueError(f"condition is longer than {MAX_CONDITION_CHARS} characters")
    return _Parser(src).parse()


# --------------------------------------------------------------------------- evaluation

_MISSING = object()


def _lookup(args: Mapping[str, Any], path: tuple[str, ...], tool: str | None) -> Any:
    if len(path) > 1 and tool is not None and path[0] == tool and path[0] not in args:
        path = path[1:]  # "issue_refund.amount" on the issue_refund tool
    cur: Any = args
    for seg in path:
        if isinstance(cur, Mapping):
            cur = cur.get(seg, _MISSING)
        elif isinstance(cur, Sequence) and not isinstance(cur, str | bytes) and seg.isdigit():
            idx = int(seg)
            cur = cur[idx] if idx < len(cur) else _MISSING
        elif not seg.startswith("_") and not seg.isdigit():
            cur = getattr(cur, seg, _MISSING)  # dataclasses, pydantic models, simple objects
        else:
            cur = _MISSING
        if cur is _MISSING:
            return _MISSING
    return cur


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        f = float(value)
        return f if not math.isnan(f) else None
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _equal(actual: Any, lit: Literal) -> bool:
    if lit is None:
        return actual is None
    if isinstance(lit, bool):
        return isinstance(actual, bool) and actual is lit
    if isinstance(lit, int | float):
        num = _as_number(actual)
        return num is not None and num == float(lit)
    return isinstance(actual, str) and actual == lit


def _compare(actual: Any, op: str, lit: Literal) -> bool:
    if op == "==":
        return _equal(actual, lit)
    if op == "!=":
        return not _equal(actual, lit)
    if isinstance(lit, int | float) and not isinstance(lit, bool):
        num = _as_number(actual)
        if num is None:
            return False
        a: Any = num
        b: Any = float(lit)
    elif isinstance(lit, str) and isinstance(actual, str):
        a, b = actual, lit
    else:
        return False
    if op == ">":
        return bool(a > b)
    if op == ">=":
        return bool(a >= b)
    if op == "<":
        return bool(a < b)
    return bool(a <= b)


def fmt(value: Any) -> str:
    """A short, readable rendering of a value for block reasons."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float | Decimal):
        f = float(value)
        if math.isfinite(f) and f.is_integer() and abs(f) < 1e15:
            return str(int(f))
        return repr(f) if isinstance(value, float) else str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        s = value if len(value) <= 80 else value[:77] + "..."
        return json.dumps(s, ensure_ascii=False)
    if isinstance(value, tuple | list):
        return "[" + ", ".join(fmt(v) for v in value) + "]"
    s = str(value)
    return s if len(s) <= 80 else s[:77] + "..."


def evaluate(node: Condition, args: Mapping[str, Any], tool: str | None = None) -> list[str] | None:
    """None when the condition is false; else the comparisons that made it true, rendered
    (e.g. ``["amount 489 > 200"]``)."""
    if isinstance(node, BoolOp):
        if node.op == "and":
            parts: list[str] = []
            for item in node.items:
                got = evaluate(item, args, tool)
                if got is None:
                    return None
                parts.extend(got)
            return parts
        for item in node.items:
            got = evaluate(item, args, tool)
            if got is not None:
                return got
        return None
    actual = _lookup(args, node.path, tool)
    if actual is _MISSING:
        return None
    if node.op in ("in", "not in"):
        values = node.value if isinstance(node.value, tuple) else (node.value,)
        hit = any(_equal(actual, v) for v in values)
        ok = hit if node.op == "in" else not hit
    else:
        assert not isinstance(node.value, tuple)
        ok = _compare(actual, node.op, node.value)
    if not ok:
        return None
    return [f"{'.'.join(node.path)} {fmt(actual)} {node.op} {fmt(node.value)}"]
