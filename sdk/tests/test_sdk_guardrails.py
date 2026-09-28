"""SDK guardrails and budgets (M3 step 4): prompt/tool blocks, redaction before export, budget
alerts and stops, the background rules/prices fetch, local rules, and the span contract."""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Iterator
from typing import Any

import anthropic
import httpx2
import openai
import pytest
from conftest import (
    FAKE_KEY,
    Ingest,
    Provider,
    anthropic_message,
    anthropic_stream,
    json_response,
    openai_completion,
    openai_stream_response,
    rules_doc,
    server_price_table,
    spans_of,
    sse_response,
    start_sdk,
    wait_config_fetched,
)

import lucentpad
from lucentpad import _core, _llm
from lucentpad._pricing import cost_usd, parse_price_table
from lucentpad.guardrails import Rule
from lucentpad_server import pricing, schema
from lucentpad_server.schema import Attr, EventName

MODEL = "claude-sonnet-5"
EMAIL = "maya.patel@example.com"
ANT_KEY = "sk-ant-api03-" + "Zx9Kq2Lm7Np4Rt6Vw8Yb1Cd3Fg5Hj0Ks2Mn4Pq6St"
CARD = "4111 1111 1111 1111"

WIRE = {
    "id": "no-wire",
    "type": "prompt",
    "keywords": ["wire transfer"],
    "message": "We never move money by wire.",
}
REFUND = {
    "id": "refund_limit",
    "type": "tool",
    "tool": "issue_refund",
    "condition": "amount > 200",
    "message": "Refunds over $200 need a human to approve them.",
}
BLOCKED_MSGS: list[Any] = [{"role": "user", "content": "Please send a wire transfer to me"}]
OK_MSGS: list[Any] = [{"role": "user", "content": "Where's order 1042?"}]


def check_api_requests(fake: Ingest) -> None:
    assert not fake.invalid, fake.invalid
    for request in [*fake.requests, *fake.api_requests]:  # no credentials, ever
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        assert FAKE_KEY not in request.content.decode()


@pytest.fixture
def api() -> Iterator[Ingest]:
    fake = Ingest(rules=rules_doc(WIRE, REFUND))
    start_sdk(fake, wait_config=True)
    yield fake
    lucentpad.flush(2.0)
    lucentpad.shutdown()
    check_api_requests(fake)


def anth(provider: Provider) -> anthropic.Anthropic:
    return lucentpad.wrap(
        anthropic.Anthropic(
            api_key=FAKE_KEY,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(
                transport=httpx2.MockTransport(provider.handler)
            ),
        )
    )


def aanth(provider: Provider) -> anthropic.AsyncAnthropic:
    return lucentpad.wrap(
        anthropic.AsyncAnthropic(
            api_key=FAKE_KEY,
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(provider.handler)
            ),
        )
    )


def oai(provider: Provider) -> openai.OpenAI:
    return lucentpad.wrap(
        openai.OpenAI(
            api_key=FAKE_KEY,
            max_retries=0,
            http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(provider.handler)),
        )
    )


def aoai(provider: Provider) -> openai.AsyncOpenAI:
    return lucentpad.wrap(
        openai.AsyncOpenAI(
            api_key=FAKE_KEY,
            max_retries=0,
            http_client=openai.DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(provider.handler)
            ),
        )
    )


def by_kind(spans: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return sorted((s for s in spans if s["kind"] == kind), key=lambda s: s["start_time"])


def events(span: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [e["attributes"] for e in span["events"] if e["name"] == name]


def redactions(span: dict[str, Any]) -> dict[str, int]:
    return {
        e[Attr.REDACTION_KIND]: e[Attr.REDACTION_COUNT] for e in events(span, EventName.REDACTION)
    }


def assert_block_span(span: dict[str, Any], rule: str, reason: str, parent: str | None) -> None:
    schema.Span.model_validate(span)
    assert span["kind"] == "guardrail"
    assert span["status"] == "blocked"
    assert span["name"] == f"guardrail {rule}"
    assert span["parent_span_id"] == parent
    assert span["source"] == "sdk"
    a = span["attributes"]
    assert a[Attr.GUARDRAIL_RULE] == rule
    assert a[Attr.GUARDRAIL_REASON] == reason
    assert a[Attr.CLIENT] == "sdk"
    assert span["status_message"] == reason
    assert span["events"][0]["name"] == EventName.GUARDRAIL_BLOCK
    assert span["events"][0]["attributes"] == {Attr.GUARDRAIL_RULE: rule}
    assert span["events"][0]["time"] == span["start_time"]


WIRE_REASON = 'keyword "wire transfer": We never move money by wire.'
REFUND_REASON = "amount 489 > 200: Refunds over $200 need a human to approve them."


# --------------------------------------------------------------------------- prompt rules


@pytest.mark.parametrize("how", ["create", "create_stream", "stream_manager"])
def test_prompt_block_anthropic(api: Ingest, how: str) -> None:
    provider = Provider([])  # any request would fail the test (nothing scripted)
    client = anth(provider)
    with lucentpad.trace("run") as t:
        with pytest.raises(lucentpad.GuardrailBlocked) as info:
            if how == "create":
                client.messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
            elif how == "create_stream":
                client.messages.create(
                    model=MODEL, max_tokens=5, messages=BLOCKED_MSGS, stream=True
                )
            else:
                with client.messages.stream(
                    model=MODEL, max_tokens=5, messages=BLOCKED_MSGS
                ) as stream:
                    list(stream)
    assert info.value.rule == "no-wire"
    assert info.value.reason == WIRE_REASON
    assert str(info.value) == f"blocked by guardrail no-wire: {WIRE_REASON}"
    assert provider.requests == []
    spans = spans_of(api, 2)
    (root,) = by_kind(spans, "agent")
    (block,) = by_kind(spans, "guardrail")
    assert root["trace_id"] == block["trace_id"] == t.trace_id
    assert_block_span(block, "no-wire", WIRE_REASON, root["span_id"])
    assert block["attributes"][Attr.INPUT_PREVIEW] == BLOCKED_MSGS[0]["content"]
    assert root["attributes"][Attr.INPUT_PREVIEW] == BLOCKED_MSGS[0]["content"]
    assert root["status"] == "ok"  # the agent caught the block


@pytest.mark.parametrize("stream", [False, True])
def test_prompt_block_openai(api: Ingest, stream: bool) -> None:
    provider = Provider([])
    client = oai(provider)
    msgs = [{"role": "system", "content": "be nice"}, *BLOCKED_MSGS]
    with lucentpad.trace("run"), pytest.raises(lucentpad.GuardrailBlocked):
        client.chat.completions.create(model="gpt-5", messages=msgs, stream=stream)
    assert provider.requests == []
    spans = spans_of(api, 2)
    (root,) = by_kind(spans, "agent")
    assert_block_span(by_kind(spans, "guardrail")[0], "no-wire", WIRE_REASON, root["span_id"])


async def test_prompt_block_async_clients(api: Ingest) -> None:
    ap, op = Provider([]), Provider([])
    a, o = aanth(ap), aoai(op)
    async with lucentpad.trace("run"):
        with pytest.raises(lucentpad.GuardrailBlocked):
            await a.messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
        with pytest.raises(lucentpad.GuardrailBlocked):
            await a.messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS, stream=True)
        with pytest.raises(lucentpad.GuardrailBlocked):
            a.messages.stream(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
        with pytest.raises(lucentpad.GuardrailBlocked):
            await o.chat.completions.create(model="gpt-5", messages=BLOCKED_MSGS, stream=True)
    assert ap.requests == [] and op.requests == []
    spans = spans_of(api, 5)
    assert len(by_kind(spans, "guardrail")) == 4


def test_prompt_rules_check_the_last_user_message_only(api: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("ok"))] * 2)
    client = anth(provider)
    text_blocks: list[Any] = [
        {"role": "user", "content": [{"type": "text", "text": "a WIRE TRANSFER?"}]}
    ]
    with pytest.raises(lucentpad.GuardrailBlocked):
        client.messages.create(model=MODEL, max_tokens=5, messages=text_blocks)
    history = [*BLOCKED_MSGS, {"role": "assistant", "content": "no"}, *OK_MSGS]
    client.messages.create(model=MODEL, max_tokens=5, messages=history)
    client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS, system="wire transfer")
    assert len(provider.requests) == 2


# --------------------------------------------------------------------------- tool rules

refunds: list[tuple[str, float]] = []


@lucentpad.span
def issue_refund(order_id: str, amount: float, currency: str = "USD") -> dict[str, Any]:
    refunds.append((order_id, amount))
    return {"ok": True}


@lucentpad.span(name="issue_refund")
async def issue_refund_async(order_id: str, **details: Any) -> dict[str, Any]:
    refunds.append((order_id, details["amount"]))
    return {"ok": True}


@lucentpad.span(name="issue_refund", kind="agent")
def not_a_tool(amount: float) -> str:
    return "ran"


def test_tool_block_via_span(api: Ingest) -> None:
    refunds.clear()
    with lucentpad.trace("run"):
        with pytest.raises(lucentpad.GuardrailBlocked) as info:
            issue_refund("1057", 489)
        with pytest.raises(lucentpad.GuardrailBlocked):
            issue_refund(order_id="1057", amount="489.00")  # type: ignore[arg-type]
        assert issue_refund("1042", amount=77.0) == {"ok": True}
        assert not_a_tool(5000) == "ran"  # tool rules apply to kind="tool" spans only
    assert info.value.rule == "refund_limit"
    assert info.value.reason == REFUND_REASON
    assert refunds == [("1042", 77.0)]  # the blocked bodies never ran
    spans = spans_of(api, 5)
    (root,) = by_kind(spans, "agent")[:1]
    blocks = by_kind(spans, "guardrail")
    assert len(blocks) == 2
    assert_block_span(blocks[0], "refund_limit", REFUND_REASON, root["span_id"])
    assert blocks[0]["attributes"][Attr.GEN_AI_TOOL_NAME] == "issue_refund"
    assert blocks[1]["attributes"][Attr.GUARDRAIL_REASON].startswith('amount "489.00" > 200')
    tools = by_kind(spans, "tool")
    assert [(s["name"], s["status"]) for s in tools] == [("issue_refund", "ok")]  # no blocked span


async def test_tool_block_async_and_kwargs(api: Ingest) -> None:
    refunds.clear()
    async with lucentpad.trace("run"):
        with pytest.raises(lucentpad.GuardrailBlocked):
            await issue_refund_async("1057", amount=489, note="x")
        await issue_refund_async("1042", amount=10)
    assert refunds == [("1042", 10)]
    spans = spans_of(api, 3)
    assert len(by_kind(spans, "guardrail")) == 1


# --------------------------------------------------------------------------- redaction


def test_redaction_in_previews_with_events(api: Ingest) -> None:
    provider = Provider(
        [json_response(anthropic_message(f"I'll email {EMAIL} and refund card {CARD}."))]
    )
    client = anth(provider)
    msgs: list[Any] = [{"role": "user", "content": f"My key is {ANT_KEY}, email {EMAIL}"}]
    with lucentpad.trace("run"):
        client.messages.create(model=MODEL, max_tokens=5, messages=msgs)
    # what goes to the provider is never altered (D20)
    assert ANT_KEY in provider.requests[0].content.decode()
    spans = spans_of(api, 2)
    exported = json.dumps(spans) + "".join(r.content.decode() for r in api.requests)
    for secret in (EMAIL, ANT_KEY, CARD, "sk-ant"):
        assert secret not in exported
    (llm,) = by_kind(spans, "llm")
    a = llm["attributes"]
    assert a[Attr.INPUT_PREVIEW] == "My key is [REDACTED:api_key], email [REDACTED:email]"
    assert a[Attr.OUTPUT_PREVIEW] == "I'll email [REDACTED:email] and refund card [REDACTED:card]."
    assert redactions(llm) == {"api_key": 1, "email": 2, "card": 1}
    (root,) = by_kind(spans, "agent")
    assert root["attributes"][Attr.INPUT_PREVIEW] == a[Attr.INPUT_PREVIEW]
    assert root["events"] == []  # the folded copy is already clean: counted once


def test_redaction_of_streamed_outputs(api: Ingest) -> None:
    chunks = ["Contact maya.pa", "tel@example.com", " or card 4111-1111-", "1111-1111 today"]
    ap = Provider([sse_response(anthropic_stream(chunks))])
    op = Provider([openai_stream_response(chunks)])
    with lucentpad.trace("run"):
        for _ in anth(ap).messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS, stream=True):
            pass
        for _ in oai(op).chat.completions.create(model="gpt-5", messages=OK_MSGS, stream=True):
            pass
    spans = spans_of(api, 3)
    for llm in by_kind(spans, "llm"):
        assert llm["attributes"][Attr.OUTPUT_PREVIEW] == (
            "Contact [REDACTED:email] or card [REDACTED:card] today"
        )
        assert redactions(llm) == {"email": 1, "card": 1}
    assert EMAIL not in json.dumps(spans)


def test_redaction_of_every_string_the_sdk_writes(api: Ingest) -> None:
    with (
        pytest.raises(ValueError, match="could not email"),
        lucentpad.trace("run", input=f"from {EMAIL}", note=f"card {CARD}", tags=[EMAIL]) as t,
    ):
        lucentpad.set_attribute("customer", EMAIL)
        t.set_output(f"sent to {EMAIL}")
        raise ValueError(f"could not email {EMAIL}")
    (root,) = spans_of(api, 1)
    a = root["attributes"]
    assert a[Attr.INPUT_PREVIEW] == "from [REDACTED:email]"
    assert a[Attr.OUTPUT_PREVIEW] == "sent to [REDACTED:email]"
    assert a["note"] == "card [REDACTED:card]"
    assert a["tags"] == ["[REDACTED:email]"]
    assert a["customer"] == "[REDACTED:email]"
    assert root["status_message"] == "ValueError: could not email [REDACTED:email]"
    assert redactions(root) == {"email": 5, "card": 1}


def test_secret_straddling_the_preview_cut_is_redacted(api: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("ok"))])
    long = "x" * 1990 + " " + ANT_KEY + " tail"
    anth(provider).messages.create(
        model=MODEL, max_tokens=5, messages=[{"role": "user", "content": long}]
    )
    (llm,) = spans_of(api, 1)
    preview = llm["attributes"][Attr.INPUT_PREVIEW]
    assert preview == "x" * 1990 + " "  # cut before the marker, never inside it
    assert llm["attributes"][Attr.INPUT_TRUNCATED] is True
    assert redactions(llm) == {"api_key": 1}


def test_redaction_runs_without_content_capture_too() -> None:
    fake = Ingest()
    start_sdk(fake, capture_content=False)
    with lucentpad.trace("run", input=f"from {EMAIL}"):
        lucentpad.set_attribute("customer", EMAIL)
    (root,) = spans_of(fake, 1)
    assert Attr.INPUT_PREVIEW not in root["attributes"]
    assert root["attributes"]["customer"] == "[REDACTED:email]"


def test_redaction_failure_drops_previews_never_exports_raw(
    api: Ingest, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(text: str) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(_core, "redact", boom)
    with lucentpad.trace("run", input=f"from {EMAIL}"):
        pass
    (root,) = spans_of(api, 1)
    assert Attr.INPUT_PREVIEW not in root["attributes"]


# --------------------------------------------------------------------------- budgets

# anthropic_message() usage is (25 in, 12 out) on claude-sonnet-5 (2 / 10 per MTok):
PER_CALL = (25 * 2 + 12 * 10) / 1_000_000  # 0.00017


def test_budget_alert_on_the_crossing_call(api: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("a"))] * 3)
    client = anth(provider)
    with lucentpad.trace("run", budget_usd=0.0003):
        for _ in range(3):
            client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
    assert len(provider.requests) == 3  # alert mode never stops
    spans = spans_of(api, 4)
    llm = by_kind(spans, "llm")
    alerts = [events(s, EventName.BUDGET_ALERT) for s in llm]
    assert alerts[0] == [] and alerts[2] == []
    assert alerts[1] == [
        {
            Attr.BUDGET_LIMIT_USD: 0.0003,
            Attr.BUDGET_SPENT_USD: round(2 * PER_CALL, 6),
            Attr.BUDGET_SCOPE: "run",
        }
    ]


def test_budget_stop_raises_on_the_next_call(api: Ingest) -> None:
    ap = Provider([json_response(anthropic_message("a"))] * 5)
    op = Provider([json_response(openai_completion("b"))])
    client = anth(ap)
    with lucentpad.trace("run", budget_usd=0.0003, on_budget="stop"):
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)  # crosses: alert
        with pytest.raises(lucentpad.BudgetExceeded) as info:
            client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
        with pytest.raises(lucentpad.BudgetExceeded):
            oai(op).chat.completions.create(model="gpt-5", messages=OK_MSGS)
        with pytest.raises(lucentpad.BudgetExceeded):
            client.messages.stream(model=MODEL, max_tokens=5, messages=OK_MSGS)
    assert len(ap.requests) == 2 and op.requests == []
    assert info.value.limit_usd == 0.0003
    assert info.value.spent_usd == round(2 * PER_CALL, 6)
    llm = by_kind(spans_of(api, 3), "llm")
    assert len(llm) == 2 and events(llm[1], EventName.BUDGET_ALERT)
    # a new run starts with a fresh budget
    ap.responses.append(json_response(anthropic_message("c")))
    with lucentpad.trace("next", budget_usd=0.0003, on_budget="stop"):
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)


def test_budget_uncaught_stop_marks_the_run(api: Ingest) -> None:
    provider = Provider([sse_response(anthropic_stream(["hi"]))])
    client = anth(provider)
    # the stream's usage (31 in, 17 out) = 0.000232 > 0.0002: the next call is stopped
    with (
        pytest.raises(lucentpad.BudgetExceeded),
        lucentpad.trace("run", budget_usd=0.0002, on_budget="stop"),
    ):
        list(client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS, stream=True))
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
    spans = spans_of(api, 2)
    (root,) = by_kind(spans, "agent")
    assert root["status"] == "error" and "BudgetExceeded" in root["status_message"]
    (llm,) = by_kind(spans, "llm")
    assert events(llm, EventName.BUDGET_ALERT)[0][Attr.BUDGET_SPENT_USD] == 0.000232


def test_budget_prices_cache_tokens(api: Ingest) -> None:
    body = anthropic_message("a")
    body["usage"] = {
        "input_tokens": 10,
        "output_tokens": 10,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 100,
    }
    provider = Provider([json_response(body)])
    with lucentpad.trace("run", budget_usd=0.0005):
        anth(provider).messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
    (llm,) = by_kind(spans_of(api, 2), "llm")
    expected = pricing.cost_usd("claude-sonnet-5-20260901", 1110, 10, 1000, 100)
    assert expected == 0.00057
    assert events(llm, EventName.BUDGET_ALERT)[0][Attr.BUDGET_SPENT_USD] == expected


def test_default_budget_applies_to_top_level_runs_once() -> None:
    fake = Ingest()
    start_sdk(fake, default_budget_usd=0.0001, wait_config=True)
    provider = Provider([json_response(anthropic_message("a"))] * 2)
    client = anth(provider)
    with lucentpad.trace("outer"):
        with lucentpad.trace("inner"):
            client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
    llm = by_kind(spans_of(fake, 4), "llm")
    assert [len(events(s, EventName.BUDGET_ALERT)) for s in llm] == [1, 0]
    assert events(llm[0], EventName.BUDGET_ALERT)[0][Attr.BUDGET_LIMIT_USD] == 0.0001


def test_budget_ignores_unpriced_models_and_bad_budgets(api: Ingest) -> None:
    provider = Provider([json_response(anthropic_message("a", model="claude-mystery-1"))])
    with lucentpad.trace("run", budget_usd=0.0):
        anth(provider).messages.create(model="claude-mystery-1", max_tokens=5, messages=OK_MSGS)
    with lucentpad.trace("bad", budget_usd=float("nan"), on_budget="explode"):  # type: ignore[arg-type]
        pass
    llm = by_kind(spans_of(api, 3), "llm")
    assert events(llm[0], EventName.BUDGET_ALERT) == []


def test_sdk_cost_formula_matches_server_pricing() -> None:
    table = parse_price_table(server_price_table())
    rng = random.Random(7)  # noqa: S311 - test data
    models = [*pricing.PRICES, "claude-haiku-4-5-20251001", "gpt-5-2026-08-07", "unknown-model"]
    for _ in range(500):
        model = rng.choice(models)
        inp = rng.randint(0, 200_000)
        cr = rng.randint(0, inp)
        cw = rng.randint(0, inp - cr)
        out = rng.randint(0, 20_000)
        assert cost_usd(table, model, inp, out, cr, cw) == pricing.cost_usd(model, inp, out, cr, cw)


# --------------------------------------------------------------------------- fetch behaviour


@pytest.mark.parametrize("mode", ["down", "501"])
def test_api_down_or_501_no_rules_redaction_still_runs(mode: str) -> None:
    fake = Ingest(rules=rules_doc(WIRE, REFUND))
    if mode == "down":
        fake.api_down = True
    else:
        fake.rules = fake.pricing = None
    start_sdk(fake, wait_config=True)
    provider = Provider([json_response(anthropic_message(f"mail {EMAIL}"))])
    refunds.clear()
    with lucentpad.trace("run", budget_usd=0.0):
        anth(provider).messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
        issue_refund("1057", 489)
    assert len(provider.requests) == 1 and refunds == [("1057", 489)]  # no rules
    spans = spans_of(fake, 3)
    (llm,) = by_kind(spans, "llm")
    assert llm["attributes"][Attr.OUTPUT_PREVIEW] == "mail [REDACTED:email]"
    assert events(llm, EventName.BUDGET_ALERT) == []  # no price table: budgets skipped
    assert _core.current_prices() is None
    check_api_requests(fake)


def test_last_known_rules_and_prices_survive_outages(api: Ingest) -> None:
    remote = _core.current_remote()
    assert remote is not None
    assert _core.current_rules().prompt and _core.current_prices()
    api.api_down = True
    assert remote.fetch_once() is False
    assert _core.current_rules().prompt and _core.current_prices()  # kept
    api.api_down, api.api_status = False, 503
    remote.fetch_once()
    assert _core.current_rules().prompt and _core.current_prices()  # kept
    api.api_status = 501  # the server says: no rules configured
    assert remote.fetch_once() is True
    assert _core.current_rules().empty
    assert _core.current_prices()  # the price table is kept


def test_rules_refresh_in_the_background() -> None:
    fake = Ingest()  # no rules at first (501)
    start_sdk(fake, refresh_interval=0.02, wait_config=True)
    provider = Provider([json_response(anthropic_message("a"))])
    client = anth(provider)
    client.messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
    fake.rules = rules_doc(WIRE, version="v2")
    remote = _core.current_remote()
    assert remote is not None
    wait_config_fetched(remote.fetches + 2)
    with pytest.raises(lucentpad.GuardrailBlocked):
        client.messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
    assert fake.api_paths().count("/v1/guardrails/rules") >= 3


def test_first_call_waits_briefly_for_the_first_rules_fetch() -> None:
    fake = Ingest(rules=rules_doc(WIRE), api_delay=0.1)
    start_sdk(fake)  # no waiting here: init never blocks
    provider = Provider([])
    with pytest.raises(lucentpad.GuardrailBlocked):
        anth(provider).messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)


def test_invalid_rules_from_the_api_are_ignored(caplog: pytest.LogCaptureFixture) -> None:
    bad = {"rules": [{"id": "x", "type": "prompt", "message": "m"}], "source": "s", "version": "1"}
    fake = Ingest(rules=bad)
    with caplog.at_level(logging.WARNING, logger="lucentpad"):
        start_sdk(fake, wait_config=True)
    assert _core.current_rules().empty
    assert "ignoring invalid guardrail rules" in caplog.text
    provider = Provider([json_response(anthropic_message("a"))])
    anth(provider).messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)


def test_guardrail_internal_errors_never_reach_the_host(
    api: Ingest, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(_llm, "check_prompt", boom)
    monkeypatch.setattr(_core, "check_tool", boom)
    provider = Provider([json_response(anthropic_message("a"))])
    anth(provider).messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
    refunds.clear()
    issue_refund("1057", 489)
    assert len(provider.requests) == 1 and refunds == [("1057", 489)]


# --------------------------------------------------------------------------- local rules


@pytest.mark.parametrize(
    "local",
    [
        {"rules": [WIRE]},
        [WIRE],
        [Rule(id="no-wire", type="prompt", message=WIRE["message"], keywords=("wire transfer",))],  # type: ignore[arg-type]
    ],
)
def test_local_rules_replace_the_apis(local: Any) -> None:
    fake = Ingest(rules=rules_doc(REFUND))
    start_sdk(fake, rules=local, wait_config=True)
    refunds.clear()
    with pytest.raises(lucentpad.GuardrailBlocked):
        anth(Provider([])).messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
    issue_refund("1057", 489)  # the API's tool rule is not used
    assert refunds == [("1057", 489)]
    assert "/v1/guardrails/rules" not in fake.api_paths()
    assert "/v1/pricing" in fake.api_paths()  # prices still come from the API


def test_init_rejects_invalid_local_rules() -> None:
    with pytest.raises(ValueError, match="rule 'x'"):
        lucentpad.init(
            "http://lucentpad.test", rules=[{"id": "x", "type": "prompt", "message": "m"}]
        )
    assert _core.active() is None
    lucentpad.init("http://lucentpad.test", rules=[{"id": "x"}], enabled=False)  # disabled: no-op


# --------------------------------------------------------------------------- contract


def test_every_guardrail_span_shape_validates(api: Ingest) -> None:
    provider = Provider([json_response(anthropic_message(f"to {EMAIL}, card {CARD}"))] * 2)
    client = anth(provider)
    with lucentpad.trace("contract", budget_usd=0.0001):
        with pytest.raises(lucentpad.GuardrailBlocked):
            client.messages.create(model=MODEL, max_tokens=5, messages=BLOCKED_MSGS)
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
        with pytest.raises(lucentpad.GuardrailBlocked):
            issue_refund("1057", 489)
        client.messages.create(model=MODEL, max_tokens=5, messages=OK_MSGS)
    spans = spans_of(api, 5)
    for raw in spans:
        schema.Span.model_validate(raw)
    names = {e["name"] for s in spans for e in s["events"]}
    assert names == {EventName.GUARDRAIL_BLOCK, EventName.REDACTION, EventName.BUDGET_ALERT}
    assert {s["kind"] for s in spans} == {"agent", "llm", "guardrail"}
    assert not api.invalid
