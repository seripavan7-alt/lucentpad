"""Gateway guardrails (M3 step 3): prompt rules block before the upstream is called (provider-
shaped 400 + a guardrail span), recorded turns are redacted, per-session budget alerts. No
network, no database."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.guardrails.rules import rules_version
from lucentpad_server.pricing import cost_usd
from lucentpad_server.schema import Attr, EventName, GuardrailRule, GuardrailRules, Span

from .gateway_fakes import (
    ANTHROPIC_MESSAGE,
    ANTHROPIC_STREAM,
    API_KEY,
    FakeStream,
    FakeUpstream,
    anthropic_headers,
    asgi_client,
    chop,
    json_reply,
    make_gateway,
    openai_headers,
    sse_reply,
)

MESSAGES = "/gateway/anthropic/v1/messages"
CHAT = "/gateway/openai/v1/chat/completions"
EMAIL = "maya.patel@example.com"


class StaticRules:
    """A ``GuardrailProvider`` with fixed rules (swappable to test rule changes)."""

    def __init__(self, *rules: GuardrailRule) -> None:
        self.set(*rules)

    def set(self, *rules: GuardrailRule) -> None:
        self._rules = GuardrailRules(
            rules=list(rules), source="test", version=rules_version(list(rules))
        )

    def rules(self) -> GuardrailRules:
        return self._rules


NO_DROP = GuardrailRule(
    id="no_drop_tables",
    type="prompt",
    keywords=["drop table"],
    message="Destructive SQL is not allowed here.",
)
REFUND = GuardrailRule(
    id="refund_limit", type="tool", tool="issue_refund", condition="amount > 200", message="m"
)


def _anthropic(text: str | list[dict[str, Any]], stream: bool = False) -> bytes:
    return json.dumps(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 100,
            "stream": stream,
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": text},
            ],
        }
    ).encode()


def _openai(text: str) -> bytes:
    return json.dumps(
        {
            "model": "gpt-5",
            "messages": [
                {"role": "system", "content": "You are Copilot. Never drop table users."},
                {"role": "user", "content": text},
            ],
        }
    ).encode()


def _guardrail_spans(spans: list[Span]) -> list[Span]:
    return [s for s in spans if s.kind == "guardrail"]


# --------------------------------------------------------------------------- prompt rules


@pytest.mark.parametrize("stream", [False, True])
async def test_anthropic_prompt_block(stream: bool) -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, ingest = make_gateway(upstream)
    app.state.guardrails = StaticRules(REFUND, NO_DROP)
    async with asgi_client(app) as client:
        r = await client.post(
            MESSAGES,
            headers=anthropic_headers(),
            content=_anthropic(
                [
                    {"type": "tool_result", "tool_use_id": "t", "content": "ok"},
                    {"type": "text", "text": f"Now DROP TABLE users; I'm {EMAIL}"},
                ],
                stream=stream,
            ),
        )
    await proxy.drain()

    assert r.status_code == 400
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Blocked by LucentPad guardrail no_drop_tables: "
            "Destructive SQL is not allowed here.",
        },
    }
    assert upstream.requests == []  # never forwarded
    assert proxy.blocked_requests == 1

    (root,) = ingest.roots()
    (g,) = _guardrail_spans(ingest.spans)
    assert ingest.llm() == []
    assert g.trace_id == root.trace_id and g.parent_span_id == root.span_id
    assert g.name == "guardrail no_drop_tables"
    assert g.status == "blocked" and g.source == "gateway"
    assert g.status_message == g.attributes[Attr.GUARDRAIL_REASON]
    reason = g.attributes[Attr.GUARDRAIL_REASON]
    assert isinstance(reason, str) and "Destructive SQL" in reason
    a = g.attributes
    assert a[Attr.GUARDRAIL_RULE] == "no_drop_tables"
    assert a[Attr.CLIENT] == "claude-code"
    assert a[Attr.GEN_AI_REQUEST_MODEL] == "claude-sonnet-5"
    # The blocked prompt is kept as a preview, redacted; the key never appears.
    assert a[Attr.INPUT_PREVIEW] == "Now DROP TABLE users; I'm [REDACTED:email]"
    assert API_KEY not in g.model_dump_json() and EMAIL not in g.model_dump_json()
    names = [e.name for e in g.events]
    assert names == [EventName.GUARDRAIL_BLOCK, EventName.REDACTION]
    assert g.events[0].attributes[Attr.GUARDRAIL_RULE] == "no_drop_tables"
    Span.model_validate(g.model_dump())


async def test_openai_prompt_block() -> None:
    upstream = FakeUpstream([json_reply({"choices": []})])
    app, proxy, ingest = make_gateway(upstream)
    app.state.guardrails = StaticRules(NO_DROP)
    async with asgi_client(app) as client:
        r = await client.post(
            CHAT, headers=openai_headers(), content=_openai("please drop table orders")
        )
    await proxy.drain()
    assert r.status_code == 400
    assert r.json() == {
        "error": {
            "message": "Blocked by LucentPad guardrail no_drop_tables: "
            "Destructive SQL is not allowed here.",
            "type": "invalid_request_error",
            "code": "guardrail_blocked",
        }
    }
    assert upstream.requests == []
    (g,) = _guardrail_spans(ingest.spans)
    assert g.attributes[Attr.CLIENT] == "copilot-chat"
    assert g.attributes[Attr.GEN_AI_SYSTEM] == "openai"


async def test_only_the_last_user_message_is_checked() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, ingest = make_gateway(upstream)
    app.state.guardrails = StaticRules(NO_DROP)
    body = json.dumps(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 10,
            "system": "never drop table anything",
            "messages": [
                {"role": "user", "content": "drop table users"},
                {"role": "assistant", "content": "I can't do that."},
                {"role": "user", "content": "ok, list the tables instead"},
            ],
        }
    ).encode()
    async with asgi_client(app) as client:
        r = await client.post(MESSAGES, headers=anthropic_headers(), content=body)
        # OpenAI: the system prompt mentions it, the user message doesn't.
        r2 = await client.post(CHAT, headers=openai_headers(), content=_openai("list tables"))
    await proxy.drain()
    assert r.status_code == 200 and r2.status_code == 200
    assert len(upstream.requests) == 2
    assert _guardrail_spans(ingest.spans) == []


async def test_no_rules_or_tool_rules_only_forward_untouched() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, _ = make_gateway(upstream)
    body = _anthropic("drop table users")
    async with asgi_client(app) as client:
        assert (await client.post(MESSAGES, headers=anthropic_headers(), content=body)).is_success
        app.state.guardrails = StaticRules(REFUND)
        assert (await client.post(MESSAGES, headers=anthropic_headers(), content=body)).is_success
    await proxy.drain()
    assert upstream.bodies == [body, body]  # the request itself is never altered


async def test_rule_changes_apply_without_restart() -> None:
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, _ = make_gateway(upstream)
    rules = StaticRules()
    app.state.guardrails = rules
    body = _anthropic("drop table users")
    async with asgi_client(app) as client:
        assert (await client.post(MESSAGES, headers=anthropic_headers(), content=body)).is_success
        rules.set(NO_DROP)
        r = await client.post(MESSAGES, headers=anthropic_headers(), content=body)
        assert r.status_code == 400
        rules.set()
        assert (await client.post(MESSAGES, headers=anthropic_headers(), content=body)).is_success
    await proxy.drain()
    assert len(upstream.requests) == 2


async def test_broken_rules_provider_fails_open() -> None:
    class Broken:
        def rules(self) -> GuardrailRules:
            raise RuntimeError("boom")

    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, _ = make_gateway(upstream)
    app.state.guardrails = Broken()
    async with asgi_client(app) as client:
        r = await client.post(
            MESSAGES, headers=anthropic_headers(), content=_anthropic("drop table users")
        )
    await proxy.drain()
    assert r.status_code == 200


# --------------------------------------------------------------------------- redaction


async def test_recorded_turns_are_redacted() -> None:
    reply = {**ANTHROPIC_MESSAGE, "content": [{"type": "text", "text": f"Emailed {EMAIL}."}]}
    upstream = FakeUpstream([json_reply(reply)])
    app, proxy, ingest = make_gateway(upstream)
    body = _anthropic(f"My email is {EMAIL}, card 4111 1111 1111 1111")
    async with asgi_client(app) as client:
        r = await client.post(MESSAGES, headers=anthropic_headers(), content=body)
    await proxy.drain()
    assert r.status_code == 200
    assert upstream.bodies == [body]  # the provider still gets the original request
    (span,) = ingest.llm()
    a = span.attributes
    assert a[Attr.INPUT_PREVIEW] == "My email is [REDACTED:email], card [REDACTED:card]"
    assert a[Attr.OUTPUT_PREVIEW] == "Emailed [REDACTED:email]."
    kinds = {
        e.attributes[Attr.REDACTION_KIND]: e.attributes[Attr.REDACTION_COUNT]
        for e in span.events
        if e.name == EventName.REDACTION
    }
    assert kinds == {"email": 2, "card": 1}


# --------------------------------------------------------------------------- budgets


def _turn_cost() -> float:
    # ANTHROPIC_STREAM: 1200 input tokens, 42 output on claude-sonnet-5.
    cost = cost_usd("claude-sonnet-5", 1200, 42)
    assert cost is not None
    return cost


def _alerts(span: Span) -> list[dict[str, Any]]:
    return [dict(e.attributes) for e in span.events if e.name == EventName.BUDGET_ALERT]


async def test_session_budget_alert_on_the_crossing_turn() -> None:
    per_turn = _turn_cost()
    budget = per_turn * 2.5  # crossed by the third turn
    streams = [FakeStream(chop(ANTHROPIC_STREAM)) for _ in range(5)]
    upstream = FakeUpstream([sse_reply(s) for s in streams])
    app, proxy, ingest = make_gateway(upstream, session_budget_usd=budget)
    headers = anthropic_headers(**{"x-claude-code-session-id": "sess-budget"})
    async with asgi_client(app) as client:
        for _ in range(5):
            r = await client.post(MESSAGES, headers=headers, content=_anthropic("go", True))
            assert r.status_code == 200
            await proxy.drain()  # turns finish in order
    turns = sorted(ingest.llm(), key=lambda s: s.start_time)
    assert len(turns) == 5 and len({t.trace_id for t in turns}) == 1
    assert [len(_alerts(t)) for t in turns] == [0, 0, 1, 0, 0]  # alert once, never blocks
    (alert,) = _alerts(turns[2])
    assert alert[Attr.BUDGET_LIMIT_USD] == budget
    assert alert[Attr.BUDGET_SPENT_USD] == round(per_turn * 3, 6)
    assert alert[Attr.BUDGET_SCOPE] == "session"
    event = next(e for e in turns[2].events if e.name == EventName.BUDGET_ALERT)
    assert event.time == turns[2].end_time


async def test_budget_counts_cache_tokens_and_is_per_session() -> None:
    usage = {
        "input_tokens": 100,
        "cache_read_input_tokens": 50_000,
        "cache_creation_input_tokens": 10_000,
        "output_tokens": 20,
    }
    message = {**ANTHROPIC_MESSAGE, "usage": usage}
    cost = cost_usd("claude-sonnet-5", 60_100, 20, 50_000, 10_000)
    assert cost is not None and cost > cost_usd("claude-sonnet-5", 100, 20)  # type: ignore[operator]
    upstream = FakeUpstream([json_reply(message)])
    app, proxy, ingest = make_gateway(upstream, session_budget_usd=cost * 1.5)
    async with asgi_client(app) as client:
        for session in ("a", "a", "b"):
            headers = anthropic_headers(**{"x-claude-code-session-id": session})
            await client.post(MESSAGES, headers=headers, content=_anthropic("go"))
            await proxy.drain()
    turns = sorted(ingest.llm(), key=lambda s: s.start_time)
    # Session a crosses on its second turn; session b has its own budget.
    assert [len(_alerts(t)) for t in turns] == [0, 1, 0]


async def test_no_budget_by_default() -> None:
    assert GatewayConfig().session_budget_usd is None
    upstream = FakeUpstream([json_reply(ANTHROPIC_MESSAGE)])
    app, proxy, ingest = make_gateway(upstream)
    async with asgi_client(app) as client:
        for _ in range(3):
            await client.post(MESSAGES, headers=anthropic_headers(), content=_anthropic("go"))
    await proxy.drain()
    assert all(_alerts(t) == [] for t in ingest.llm())


def test_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUCENTPAD_GATEWAY_SESSION_BUDGET_USD", "2.5")
    assert GatewayConfig.from_env().session_budget_usd == 2.5
    monkeypatch.setenv("LUCENTPAD_GATEWAY_SESSION_BUDGET_USD", "0")
    assert GatewayConfig.from_env().session_budget_usd is None
    monkeypatch.delenv("LUCENTPAD_GATEWAY_SESSION_BUDGET_USD")
    assert GatewayConfig.from_env().session_budget_usd is None
