"""Built-in detectors: API keys, email addresses, card numbers.

Design notes:
- Speed (< 50 us per 2 kB preview): key patterns start with a literal so the regex engine's
  fast literal search skips most of the text; emails are found from each ``@``; card candidates
  need 4 leading digits starting 2-6. ~20 us for a digit-heavy 2 kB preview on a laptop.
- Boundaries are strict to avoid false positives: a card number must not touch letters, digits,
  ``_``, ``.`` or ``-`` (so hex trace/span ids, UUID parts, decimals and ``ORD-...`` ids never
  match), must start with 2-6 (the card networks' first digits; millisecond/nanosecond epoch
  timestamps start with 1), must pass Luhn, and separated forms must look like card groupings
  (first group 4 digits, groups of 3-6, one separator kind), so dates and phone numbers don't.
- Replacements (``[REDACTED:<kind>]``) contain no digits, ``@`` or key prefixes, so running
  ``redact`` again finds nothing: it is idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ._types import Redacted, RedactionKind

__all__ = ["redact"]

_KEY = "[REDACTED:api_key]"
_EMAIL = "[REDACTED:email]"
_CARD = "[REDACTED:card]"

# --------------------------------------------------------------------------- API keys
# Each pattern begins with a literal (``sk-``, ``gh``, ``earer``...) and checks the character
# before it with a lookbehind placed after that literal, so the regex engine can use its fast
# literal search instead of trying every position.

# Anthropic (sk-ant-...), OpenAI (sk-..., sk-proj-..., sk-svcacct-..., sk-admin-...).
_SK = re.compile(r"sk-(?<![A-Za-z0-9_\-]sk-)(ant-|proj-|svcacct-|admin-)?([A-Za-z0-9_\-]{20,})")
# GitHub classic (ghp_/gho_/ghu_/ghs_/ghr_) and fine-grained (github_pat_) tokens.
_GITHUB = re.compile(r"gh(?<![A-Za-z0-9_]gh)[pousr]_[A-Za-z0-9]{30,}(?![A-Za-z0-9_])")
_GITHUB_PAT = re.compile(
    r"github_pat_(?<![A-Za-z0-9_]github_pat_)[A-Za-z0-9_]{30,}(?![A-Za-z0-9_])"
)
# AWS access key ids (long-term AKIA..., temporary ASIA...).
_AWS = re.compile(r"A(?<![A-Za-z0-9]A)(?:KIA|SIA)[A-Z0-9]{16}(?![A-Za-z0-9])")
# ``Bearer <token>`` (HTTP Authorization values, JWTs...): the token is replaced, the word kept.
_BEARER_VALUE = r"([ \t]+)([A-Za-z0-9\-._~+/]{16,}=*)"
_BEARER = re.compile(r"earer(?<=\b[Bb]earer)" + _BEARER_VALUE)
_BEARER_UPPER = re.compile(r"EARER(?<=\bBEARER)" + _BEARER_VALUE)


def _has_digit_and_letter(s: str) -> bool:
    return any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


# --------------------------------------------------------------------------- emails

_LOCAL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-")
_LOCAL_MAX = 64
_DOMAIN = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}(?![A-Za-z0-9\-])"
)


def _redact_emails(text: str) -> tuple[str, int]:
    """Find each ``@``, walk left over the local part (<= 64 chars, not glued to more local-part
    characters) and match the domain to the right. Cheaper than one big regex: work is
    proportional to the number of ``@`` signs, not to the length of the text."""
    at = text.find("@")
    if at < 0:
        return text, 0
    out: list[str] = []
    last = 0
    count = 0
    while at >= 0:
        i = at
        floor = max(last, at - _LOCAL_MAX)
        while i > floor and text[i - 1] in _LOCAL_CHARS:
            i -= 1
        glued = i > last and text[i - 1] in _LOCAL_CHARS  # local part longer than 64
        m = None if (i == at or glued) else _DOMAIN.match(text, at + 1)
        if m is None:
            at = text.find("@", at + 1)
            continue
        out.append(text[last:i])
        out.append(_EMAIL)
        last = m.end()
        count += 1
        at = text.find("@", last)
    if not count:
        return text, 0
    out.append(text[last:])
    return "".join(out), count


# --------------------------------------------------------------------------- cards

# A candidate starts with 4 digits, the first 2-6 (card networks; epoch timestamps start with 1),
# not touching word chars, dots or dashes, then >= 9 more digits optionally separated by single
# spaces/dashes. The lookbehind sits after the first digit so the engine can skip ahead to
# candidate digits quickly; at the start of the text it passes (not enough characters behind).
_CARD_CANDIDATE = re.compile(
    r"[2-6](?<![0-9A-Za-z_.\-][2-6])[0-9]{3}(?:[ \-]?[0-9]){9,}(?![0-9A-Za-z_]|[.,\-][0-9])"
)
_SEP = re.compile(r"[ \-]")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_card_digits(digits: str) -> bool:
    return 13 <= len(digits) <= 19 and digits[0] in "23456" and luhn_ok(digits)


def _redact_card_run(run: str) -> tuple[str, int]:
    """Replace the card numbers inside one candidate run; return (text, how many)."""
    if run.isdigit():
        return (_CARD, 1) if _is_card_digits(run) else (run, 0)
    groups = _SEP.split(run)
    seps = _SEP.findall(run)
    # Character offsets of each group in ``run``.
    starts: list[int] = []
    pos = 0
    for g in groups:
        starts.append(pos)
        pos += len(g) + 1
    out: list[str] = []
    found = 0
    last = 0  # offset in ``run`` copied up to
    i = 0
    n = len(groups)
    while i < n:
        hit = -1
        if _is_card_digits(groups[i]):
            # An unseparated number next to other numbers: only when spaced apart from them
            # ("qty 2 4111..."); "4111...-2" is an identifier.
            if (i == 0 or seps[i - 1] == " ") and (i == n - 1 or seps[i] == " "):
                hit = i
        elif len(groups[i]) == 4:
            for j in range(n - 1, i, -1):  # longest first
                if len(set(seps[i:j])) != 1:
                    continue
                if not all(3 <= len(groups[k]) <= 6 for k in range(i + 1, j + 1)):
                    continue
                if _is_card_digits("".join(groups[i : j + 1])):
                    hit = j
                    break
        if hit < 0:
            i += 1
            continue
        start = starts[i]
        end = starts[hit] + len(groups[hit])
        out.append(run[last:start])
        out.append(_CARD)
        last = end
        found += 1
        i = hit + 1
    if not found:
        return run, 0
    out.append(run[last:])
    return "".join(out), found


# --------------------------------------------------------------------------- redact


def _sub(
    pattern: re.Pattern[str], text: str, replace: Callable[[re.Match[str]], str | None]
) -> tuple[str, int]:
    """``pattern.sub`` where ``replace`` returns None to keep a match; returns (text, count)."""
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        new = replace(m)
        if new is None:
            return m.group(0)
        count += 1
        return new

    return pattern.sub(repl, text), count


def _sk(m: re.Match[str]) -> str | None:
    if m.group(1) or _has_digit_and_letter(m.group(2)):
        return _KEY
    return None


def _bearer(m: re.Match[str]) -> str | None:
    token = m.group(2)
    if not any(c.isdigit() for c in token):  # "Bearer authentication-flows" is prose
        return None
    return f"{m.group(0)[:5]}{m.group(1)}{_KEY}"


def redact(text: str) -> Redacted:
    """Replace API keys (Anthropic, OpenAI, GitHub, AWS access keys, bearer tokens), email
    addresses and card numbers (13-19 digits passing the Luhn check) in ``text``."""
    if not text:
        return Redacted(text)
    counts: dict[RedactionKind, int] = {}
    text, keys = _sub(_SK, text, _sk)
    text, n = _GITHUB.subn(_KEY, text)
    keys += n
    text, n = _GITHUB_PAT.subn(_KEY, text)
    keys += n
    text, n = _AWS.subn(_KEY, text)
    keys += n
    text, n = _sub(_BEARER, text, _bearer)
    keys += n
    text, n = _sub(_BEARER_UPPER, text, _bearer)
    keys += n
    if keys:
        counts["api_key"] = keys
    text, n = _redact_emails(text)
    if n:
        counts["email"] = n
    cards = 0

    def card(m: re.Match[str]) -> str:
        nonlocal cards
        new, found = _redact_card_run(m.group(0))
        cards += found
        return new

    text = _CARD_CANDIDATE.sub(card, text)
    if cards:
        counts["card"] = cards
    return Redacted(text, counts)
