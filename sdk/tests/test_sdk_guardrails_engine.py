"""Guardrail engine (``lucentpad.guardrails``): detectors, near misses, properties, rules, speed."""

from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass
from typing import Any

import pytest

from lucentpad.guardrails import (
    Block,
    Rule,
    check_prompt,
    check_tool,
    parse_rules,
    redact,
)
from lucentpad.guardrails._condition import parse_condition
from lucentpad.guardrails._redact import luhn_ok

ANTHROPIC_KEY = "sk-ant-api03-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z_-Q7" * 2 + "AA"
OPENAI_KEY = "sk-proj-" + "T3BlbkFJ9x2Yz7Qw4Er5Ty6Ui8Op0As1Df3Gh"
OPENAI_LEGACY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0uvwx"
GITHUB_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GITHUB_FINE = "github_pat_11ABCDEFG0" + "a" * 22 + "_" + "B7" * 29 + "x"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


# --------------------------------------------------------------------------- detectors


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"key={ANTHROPIC_KEY}", "key=[REDACTED:api_key]"),
        (f"OPENAI_API_KEY={OPENAI_KEY}\n", "OPENAI_API_KEY=[REDACTED:api_key]\n"),
        (f'"{OPENAI_LEGACY}"', '"[REDACTED:api_key]"'),
        (f"token {GITHUB_PAT}.", "token [REDACTED:api_key]."),
        (f"{GITHUB_FINE}", "[REDACTED:api_key]"),
        (f"aws {AWS_KEY} ok", "aws [REDACTED:api_key] ok"),
        (f"Authorization: Bearer {JWT}", "Authorization: Bearer [REDACTED:api_key]"),
        (f"authorization: bearer {JWT}", "authorization: bearer [REDACTED:api_key]"),
        (f"Authorization: Bearer {OPENAI_KEY}", "Authorization: Bearer [REDACTED:api_key]"),
    ],
)
def test_api_keys(text: str, expected: str) -> None:
    got = redact(text)
    assert got.text == expected
    assert got.counts == {"api_key": 1}
    assert got.changed


@pytest.mark.parametrize(
    "email",
    [
        "maya.patel@example.com",
        "m+orders@shop.co.uk",
        "first_last-99@mail.lucent-coffee.io",
        "UPPER.Case@Example.ORG",
    ],
)
def test_emails(email: str) -> None:
    for wrap in ("{}", "<{}>", "({})", '"{}"', "mailto:{}", "{}.", "{},"):
        text = f"reach me at {wrap.format(email)} thanks"
        got = redact(text)
        assert email not in got.text
        assert got.text == f"reach me at {wrap.format('[REDACTED:email]')} thanks"
        assert got.counts == {"email": 1}


@pytest.mark.parametrize(
    "card",
    [
        "4111111111111111",  # Visa
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "5500 0000 0000 0004",  # Mastercard
        "2221 0000 0000 0009",  # Mastercard 2-series
        "3782 822463 10005",  # Amex 4-6-5
        "378282246310005",
        "3056 930902 5904",  # Diners 4-6-4
        "6011 1111 1111 1117",  # Discover
        "4222222222222",  # 13-digit Visa
        "6250941006528599",  # UnionPay
        "4111 1111 1111 1111 003",  # 19 digits in 4-4-4-4-3
    ],
)
def test_cards(card: str) -> None:
    assert luhn_ok(card.replace(" ", "").replace("-", ""))
    for wrap in ("{}", "card: {}.", "({})", '"{}"', "{}\n", "#{}"):
        got = redact(f"pay with {wrap.format(card)} please")
        assert got.text == f"pay with {wrap.format('[REDACTED:card]')} please", wrap
        assert got.counts == {"card": 1}


def test_card_next_to_other_numbers() -> None:
    got = redact("order 1042 4111 1111 1111 1111 12 items, qty 2 4111111111111111 done")
    assert got.text == "order 1042 [REDACTED:card] 12 items, qty 2 [REDACTED:card] done"
    assert got.counts == {"card": 2}
    got = redact("2026-09-27 4111 1111 1111 1111")
    assert got.text == "2026-09-27 [REDACTED:card]"


def test_counts_several_kinds() -> None:
    text = (
        f"a@b.io and c@d.io; key {OPENAI_KEY}; cards 4111111111111111 and "
        f"5500-0000-0000-0004; aws {AWS_KEY}"
    )
    got = redact(text)
    assert got.counts == {"email": 2, "api_key": 2, "card": 2}
    for secret in ("a@b.io", "c@d.io", OPENAI_KEY, "4111111111111111", AWS_KEY):
        assert secret not in got.text


@pytest.mark.parametrize(
    "text",
    [
        # trace / span ids and other hex
        "trace 4bf92f3577b34da6a3ce929d0e0e4736 span 00f067aa0ba902b7",
        "trace 41111111111111114111111111111111",  # all-digit trace id (32 digits)
        "span 5234567890123456f",
        "id 123e4567-e89b-12d3-a456-426614174000",
        "sha a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
        "sha 4111111111111111a94a8fe5ccb19ba61c4c0873",
        # order numbers and ids
        "Where's order 1042? And #100045, ORD-4111111111111111, INV_4111111111111111",
        "order-no 4111111111111111-2",
        # dates, times, timestamps
        "2026-09-27 12:30:45.123456+00:00 and 2026-09-27T12:30:45Z",
        "2026-09-27 2026-09-28 2026-09-29",
        "epoch ms 1790000000000 ns 1790000000000000000 us 1790000000000000",
        "20260927123045 and 2026 0927 1230 45",
        # prices and decimals
        "$1,234,567.89 then 1234567890123.45 and 0.4111111111111111 and 4111111111111111.5",
        "total: 4111111111111111,50 EUR",
        # phone numbers
        "+1 (415) 555-0100, +44 20 7946 0958, +86 138 0013 8000, 415-555-0100-4567",
        # Luhn failures and non-card prefixes
        "4111 1111 1111 1112 and 4111111111111112 and 7111111111111114 and 1111111111111117",
        "0000 0000 0000 0000",
        "4111 11 1111 1111 1111",  # a short inner group
        # key look-alikes
        "sk-test-not-a-real-key and risk-assessment-for-the-quarter-2026",
        "task-sk-12345678901234567890 and sk-12345678901234567890",
        "sk-this-is-a-long-hyphenated-phrase-without-digits",
        "ghp_short and gh_pat and github_pat_short",
        "AKIA alone, ASIA PACIFIC, AKIAIOSFODNN7EXAMPLEX",
        "the bearer of bad news; Bearer tokens-are-documented-here; bearer abc",
        # email look-alikes
        "user@localhost, @mention, foo@1.2.3, name@domain, a@b.c, email me at @ noon",
        "",
    ],
)
def test_near_misses_are_left_alone(text: str) -> None:
    got = redact(text)
    assert got.text == text
    assert got.counts == {}
    assert not got.changed


def test_replacement_never_redacts_again() -> None:
    for text in ("[REDACTED:email]", "Bearer [REDACTED:api_key]", "x [REDACTED:card] y"):
        assert redact(text).text == text and not redact(text).changed


# --------------------------------------------------------------------------- property-style


def _luhn_complete(prefix: str) -> str:
    for d in "0123456789":
        if luhn_ok(prefix + d):
            return prefix + d
    raise AssertionError


def _card(rng: random.Random) -> str:
    length = rng.choice([13, 15, 16, 16, 16, 19])
    body = rng.choice("23456") + "".join(rng.choice(string.digits) for _ in range(length - 2))
    digits = _luhn_complete(body)
    style = rng.choice(["plain", "space", "dash"])
    if style == "plain" or length not in (16, 19):
        return digits
    sep = " " if style == "space" else "-"
    return sep.join(digits[i : i + 4] for i in range(0, length, 4))


def _email(rng: random.Random) -> str:
    local_chars = string.ascii_letters + string.digits + "._+-"
    local = rng.choice(string.ascii_letters) + "".join(
        rng.choice(local_chars) for _ in range(rng.randint(0, 14))
    )
    labels = [
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(2, 8)))
        for _ in range(rng.randint(1, 3))
    ]
    return f"{local}@{'.'.join(labels)}.{rng.choice(['com', 'io', 'co.uk', 'dev', 'ORG'])}"


def _b62(rng: random.Random, n: int) -> str:
    alphabet = string.ascii_letters + string.digits
    s = "".join(rng.choice(alphabet) for _ in range(n - 1))
    return s + rng.choice(string.digits)  # always at least one digit


def _key(rng: random.Random) -> str:
    kind = rng.randrange(6)
    if kind == 0:
        return "sk-ant-api03-" + _b62(rng, rng.randint(40, 95))
    if kind == 1:
        return "sk-proj-" + _b62(rng, rng.randint(40, 120))
    if kind == 2:
        return "sk-" + _b62(rng, 48)
    if kind == 3:
        return rng.choice(["ghp_", "gho_", "ghs_"]) + _b62(rng, 36)
    if kind == 4:
        return "AKIA" + "".join(
            rng.choice(string.ascii_uppercase + string.digits) for _ in range(16)
        )
    return "github_pat_" + _b62(rng, 82)


_FILLER = [
    "order",
    "1042",
    "refund",
    "$489.00",
    "2026-09-27",
    "12:30:45",
    "trace",
    "4bf92f3577b34da6a3ce929d0e0e4736",
    "00f067aa0ba902b7",
    "+1 415 555 0100",
    "ORD-4111111111111111",
    "risk-assessment",
    "@team",
    "the bearer of",
    "of",
    "1790000000000",
    "(",
    ")",
    '"',
    ":",
    "sk-short",
    "ASIA",
    "v1.2.3",
]


@dataclass
class Doc:
    text: str
    secrets: list[tuple[str, str]]  # (kind, value)


def _doc(rng: random.Random) -> Doc:
    parts: list[str] = []
    secrets: list[tuple[str, str]] = []
    for _ in range(rng.randint(1, 60)):
        r = rng.random()
        if r < 0.08:
            value, kind = _card(rng), "card"
        elif r < 0.16:
            value, kind = _email(rng), "email"
        elif r < 0.22:
            value, kind = _key(rng), "api_key"
        else:
            parts.append(rng.choice(_FILLER))
            continue
        secrets.append((kind, value))
        parts.append(rng.choice(["{}", "<{}>", "({})", '"{}"', "{}.", "{},"]).format(value))
    return Doc(" ".join(parts) if rng.random() < 0.7 else "\n".join(parts), secrets)


@pytest.mark.parametrize("seed", range(20))
def test_property_detected_values_never_survive_and_idempotent(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - test data
    for _ in range(100):
        doc = _doc(rng)
        once = redact(doc.text)
        for kind, value in doc.secrets:
            assert value not in once.text, (kind, value, doc.text)
        assert once.changed is bool(doc.secrets), doc.text  # filler alone is never redacted
        twice = redact(once.text)
        assert twice.text == once.text
        assert twice.counts == {}


# --------------------------------------------------------------------------- speed


def _typical_preview() -> str:
    lines = [
        "Customer: Hi, this is Maya Patel. Where's order 1042? It was supposed to arrive on",
        "2026-09-24 but tracking still says in transit. I paid $77.00 on 2026-09-20 at 14:02.",
        "You can reach me at maya.patel@example.com or +1 415 555 0100.",
        '[tool_result lookup_order] {"order_id": "1042", "status": "delivered", "total": 77.0,',
        '"placed": "2026-09-20T14:02:11Z", "items": [{"sku": "GRD-200", "qty": 1}],',
        '"trace": "4bf92f3577b34da6a3ce929d0e0e4736", "span": "00f067aa0ba902b7"}',
        "Agent: Thanks Maya! Order 1042 was delivered yesterday at 10:14 and left at the door.",
        "I've issued a refund of $77.00; it will show on your card in 3-5 business days.",
    ]
    text = "\n".join(lines)
    while len(text) < 2000:
        text += "\n" + "\n".join(lines)
    return text[:2000]


def _bench(text: str, n: int = 3000) -> float:
    """Best of 5 runs, microseconds per call."""
    best = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(n):
            redact(text)
        best = min(best, (time.perf_counter() - start) / n * 1e6)
    return best


def test_benchmark_typical_2kb_preview_under_50us(capsys: pytest.CaptureFixture[str]) -> None:
    text = _typical_preview()
    assert len(text) == 2000
    got = redact(text)
    assert got.counts.get("email", 0) >= 1 and "maya.patel@example.com" not in got.text
    clean = text.replace("maya.patel@example.com", "the address on file")
    us = _bench(text)
    us_clean = _bench(clean)
    with capsys.disabled():
        print(f"\nredact 2 kB preview: {us:.1f} us (with emails), {us_clean:.1f} us (clean)")
    assert us < 50, us
    assert us_clean < 50, us_clean


# --------------------------------------------------------------------------- parse_rules

DOC: dict[str, Any] = {
    "source": "built-in",
    "version": "abc",
    "rules": [
        {
            "id": "refund_limit",
            "type": "tool",
            "description": "refunds above the limit need a person",
            "keywords": [],
            "pattern": None,
            "tool": "issue_refund",
            "condition": "amount > 200",
            "message": "Refunds over $200 need a human to approve them.",
        },
        {
            "id": "no-wire",
            "type": "prompt",
            "keywords": ["wire transfer", "Western Union"],
            "message": "We never move money by wire.",
        },
        {
            "id": "ssn",
            "type": "prompt",
            "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
            "message": "Don't share social security numbers.",
        },
    ],
}


def test_parse_rules_document_and_bare_list() -> None:
    rules = parse_rules(DOC)
    assert [r.id for r in rules] == ["refund_limit", "no-wire", "ssn"]
    assert rules[0] == Rule(
        id="refund_limit",
        type="tool",
        message="Refunds over $200 need a human to approve them.",
        description="refunds above the limit need a person",
        tool="issue_refund",
        condition="amount > 200",
    )
    assert rules[1].keywords == ("wire transfer", "Western Union")
    assert parse_rules(DOC["rules"]) == rules
    assert parse_rules({"rules": []}) == []
    assert parse_rules({}) == []


@pytest.mark.parametrize(
    ("rule", "error"),
    [
        ({"type": "prompt", "keywords": ["x"], "message": "m"}, "missing id"),
        ({"id": "Bad Id", "type": "prompt", "keywords": ["x"], "message": "m"}, "'Bad Id'"),
        ({"id": "r", "type": "regex", "message": "m"}, "type must be"),
        ({"id": "r", "type": "prompt", "keywords": ["x"]}, "missing message"),
        ({"id": "r", "type": "prompt", "message": "m"}, "keywords or a pattern"),
        ({"id": "r", "type": "prompt", "pattern": "([a-", "message": "m"}, "invalid pattern"),
        ({"id": "r", "type": "prompt", "keywords": "wire", "message": "m"}, "list of strings"),
        ({"id": "r", "type": "prompt", "keywords": [""], "message": "m"}, "non-empty"),
        ({"id": "r", "type": "tool", "condition": "amount > 1", "message": "m"}, "tool name"),
        (
            {"id": "r", "type": "tool", "tool": "t", "condition": "amount >", "message": "m"},
            "invalid condition",
        ),
        (
            {
                "id": "r",
                "type": "tool",
                "tool": "t",
                "condition": "__import__('os')",
                "message": "m",
            },
            "invalid condition",
        ),
        (
            {"id": "r", "type": "tool", "tool": "t", "keywords": ["x"], "message": "m"},
            "prompt rules",
        ),
    ],
)
def test_parse_rules_rejects_bad_rules_naming_them(rule: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match="rule") as info:
        parse_rules([rule])
    assert error in str(info.value)


def test_parse_rules_rejects_duplicates_and_non_lists() -> None:
    r = {"id": "a", "type": "prompt", "keywords": ["x"], "message": "m"}
    with pytest.raises(ValueError, match="duplicate"):
        parse_rules([r, r])
    with pytest.raises(ValueError, match="list"):
        parse_rules({"rules": "nope"})


# --------------------------------------------------------------------------- check_prompt


def test_check_prompt_keywords_whole_word_case_insensitive() -> None:
    rules = parse_rules(DOC)
    block = check_prompt(rules, "Can you do a WIRE  transfer? no: a Wire Transfer please")
    assert block == Block("no-wire", 'keyword "wire transfer": We never move money by wire.')
    assert check_prompt(rules, "pay via western union") is not None
    assert check_prompt(rules, "wire transfers") is None  # whole word only
    assert check_prompt(rules, "hotwire transfer") is None
    assert check_prompt(rules, "") is None
    assert check_prompt(rules, "Where's order 1042?") is None


def test_check_prompt_pattern_and_order() -> None:
    rules = parse_rules(DOC)
    block = check_prompt(rules, "my ssn is 123-45-6789")
    assert block is not None and block.rule == "ssn"
    assert block.reason == "pattern matched: Don't share social security numbers."
    # the first matching rule wins
    assert check_prompt(rules, "wire transfer 123-45-6789") == check_prompt(
        rules[1:2], "wire transfer"
    )
    special = parse_rules(
        [{"id": "k", "type": "prompt", "keywords": ["c++", "$$$"], "message": "m"}]
    )
    assert check_prompt(special, "I love C++!") is not None
    assert check_prompt(special, "make $$$ fast") is not None


# --------------------------------------------------------------------------- check_tool


def _tool_rule(condition: str | None, tool: str = "issue_refund") -> list[Rule]:
    raw: dict[str, Any] = {"id": "r1", "type": "tool", "tool": tool, "message": "Needs a human."}
    if condition is not None:
        raw["condition"] = condition
    return parse_rules([raw])


def test_check_tool_refund_limit() -> None:
    rules = parse_rules(DOC)
    block = check_tool(rules, "issue_refund", {"order_id": "1057", "amount": 489})
    assert block == Block(
        "refund_limit", "amount 489 > 200: Refunds over $200 need a human to approve them."
    )
    assert check_tool(rules, "issue_refund", {"order_id": "1042", "amount": 77.0}) is None
    assert check_tool(rules, "issue_refund", {"order_id": "1042", "amount": 200}) is None
    assert check_tool(rules, "lookup_order", {"amount": 5000}) is None  # other tool
    assert check_tool(rules, "issue_refund", {"order_id": "1057"}) is None  # missing field
    assert check_tool(rules, "issue_refund", {"amount": 489.5}).reason.startswith(  # type: ignore[union-attr]
        "amount 489.5 > 200"
    )


@pytest.mark.parametrize(
    ("condition", "args", "blocked"),
    [
        ("amount >= 200", {"amount": 200}, True),
        ("amount < 0", {"amount": -1}, True),
        ("amount <= 10.5", {"amount": 10.5}, True),
        ("amount > 200", {"amount": "489.00"}, True),  # numeric strings
        ("amount > 200", {"amount": "lots"}, False),
        ("amount > 200", {"amount": True}, False),  # booleans are not numbers
        ("amount > 200", {"amount": None}, False),
        ("amount == 200", {"amount": 200.0}, True),
        ("currency == 'EUR'", {"currency": "EUR"}, True),
        ('currency == "EUR"', {"currency": "eur"}, False),
        ("currency != 'EUR'", {"currency": "USD"}, True),
        ("currency != 'EUR'", {}, False),  # missing field: never blocks
        ("currency != 'EUR'", {"currency": 5}, True),
        ("express == true", {"express": True}, True),
        ("express == true", {"express": 1}, False),
        ("note == null", {"note": None}, True),
        ("country in ['DE', 'FR']", {"country": "FR"}, True),
        ("country in ['DE', 'FR']", {"country": "US"}, False),
        ("country not in ['DE', 'FR']", {"country": "US"}, True),
        ("country not in ['DE', 'FR']", {}, False),
        ("qty in [1, 2, 3]", {"qty": "2"}, True),
        ("customer.country == 'DE'", {"customer": {"country": "DE"}}, True),
        ("customer.country == 'DE'", {"customer": "DE"}, False),
        ("items.0.sku == 'GRD-200'", {"items": [{"sku": "GRD-200"}]}, True),
        ("items.3.sku == 'GRD-200'", {"items": [{"sku": "GRD-200"}]}, False),
        ("issue_refund.amount > 200", {"amount": 489}, True),  # tool-qualified field
        ("amount > 200 and currency == 'USD'", {"amount": 300, "currency": "USD"}, True),
        ("amount > 200 and currency == 'USD'", {"amount": 300, "currency": "EUR"}, False),
        ("amount > 1000 or vip == false", {"amount": 5, "vip": False}, True),
        ("amount > 1000 OR vip == false", {"amount": 5, "vip": True}, False),
        ("(amount > 100 or qty > 5) and region == 'EU'", {"qty": 9, "region": "EU"}, True),
        ("amount > 100 or qty > 5 and region == 'EU'", {"amount": 101, "region": "US"}, True),
        ("name == 'O\\'Brien'", {"name": "O'Brien"}, True),
        ("amount > 1e3", {"amount": 1001}, True),
        ("code >= 'M'", {"code": "N"}, True),
    ],
)
def test_conditions(condition: str, args: dict[str, Any], blocked: bool) -> None:
    assert (check_tool(_tool_rule(condition), "issue_refund", args) is not None) is blocked


@dataclass
class Customer:
    country: str


def test_condition_on_objects_and_specific_reasons() -> None:
    rules = _tool_rule("customer.country == 'DE' and amount > 50")
    block = check_tool(rules, "issue_refund", {"customer": Customer("DE"), "amount": 51})
    assert block is not None
    assert block.reason == 'customer.country "DE" == "DE" and amount 51 > 50: Needs a human.'
    in_rule = _tool_rule("country in ['DE', 'FR']")
    b = check_tool(in_rule, "issue_refund", {"country": "FR"})
    assert b is not None and b.reason == 'country "FR" in ["DE", "FR"]: Needs a human.'


def test_tool_rule_without_condition_blocks_every_call() -> None:
    block = check_tool(_tool_rule(None, tool="delete_account"), "delete_account", {})
    assert block == Block("r1", "Needs a human.")


@pytest.mark.parametrize(
    "condition",
    [
        "",
        "amount",
        "amount > ",
        "amount >> 5",
        "amount = 5",
        "> 5",
        "amount > 5 and",
        "(amount > 5",
        "amount > 5)",
        "amount in 5",
        "amount in [5,",
        "amount not 5",
        "amount > abc",
        "amount > 'unterminated",
        "__import__('os').system('x') == 1",
        "amount > 5; drop",
        "lambda: 1",
        "(" * 40 + "a > 1" + ")" * 40,
        "a > 1 " + "and a > 1 " * 400,
    ],
)
def test_bad_conditions_raise(condition: str) -> None:
    with pytest.raises(ValueError):
        parse_condition(condition)
