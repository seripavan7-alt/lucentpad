"""Deterministic sample traffic: about a week of support-agent runs and coding-assistant sessions.

``generate(now)`` returns the same spans for the same ``now`` and ``seed``. CLI::

    python -m lucentpad_server.sample --out spans.json [--now 2026-09-25T12:00:00Z] [--seed 42]

Model names and costs come from ``pricing.py`` (list prices checked 2026-09-25).
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lucentpad_server.pricing import cost_usd
from lucentpad_server.schema import (
    PREVIEW_MAX_CHARS,
    Attr,
    Attributes,
    EventName,
    Span,
    SpanEvent,
    SpanKind,
    SpanSource,
    SpanStatus,
)

SUPPORT_SERVICE = "support-agent"
GATEWAY_SERVICE = "lucentpad-gateway"
SUPPORT_MODEL = "claude-sonnet-5"
SUPPORT_FALLBACK = "claude-haiku-4-5"
REFUND_LIMIT_USD = 200

WINDOW = timedelta(days=7)

_SUPPORT_VARIANTS: list[tuple[str, int]] = [
    ("refund", 70),  # llm, lookup_order, llm, issue_refund, draft_email, llm
    ("status", 45),  # llm, lookup_order, llm
    ("reschedule", 14),  # llm, lookup_order, llm, reschedule_delivery, llm (not shipped yet)
    ("guardrail", 8),  # refund over the limit: blocked by a guardrail span
    ("failover", 6),  # primary 429 -> fallback model
    ("budget", 6),  # crosses the per-run budget: lucentpad.budget.alert
    ("not_found", 7),  # lookup_order errors, agent apologises
    ("llm_error", 4),  # model call fails after retries; run errors
]
_GATEWAY_VARIANTS: list[tuple[str, int]] = [
    ("claude-code", 40),
    ("copilot-chat", 25),
    ("copilot-cli", 15),
]

_QUESTIONS = [
    "Where's order {order}? I want a refund.",
    "My order {order} arrived damaged, can I get my money back?",
    "Order {order} never showed up. Refund please.",
    "I was charged twice for order {order}.",
]
_STATUS_QUESTIONS = [
    "Where's order {order}?",
    "When will order {order} arrive?",
    "Has order {order} shipped yet?",
]
_RESCHEDULE_QUESTIONS = [
    "Where's order {order}? Can you reschedule delivery to 10 am tomorrow?",
    "Where's my order {order}? Could it come tomorrow at 10 am instead?",
]
_CODING_PROMPTS = [
    "Why is this test flaky?",
    "Refactor the batch writer to use COPY.",
    "Explain what this regex does.",
    "Add type hints to store.py.",
    "Write a migration that adds an index on start_time.",
    "Fix the failing CI job.",
    "Summarise the diff on this branch.",
]


_TOOL_ACTIONS = [
    "read store.py",
    "run the failing test",
    "search the repo for insert_spans",
    "open the CI log",
    "check git diff",
    "read migrations/0001_init.sql",
]
_FOLLOW_UPS = [
    "tool_result: 3 passed, 1 failed (test_writer_drains_on_shutdown)",
    "tool_result: exit code 0",
    "Looks good, can you also update the docstring?",
    "tool_result: store.py (382 lines)",
    "That didn't work, same error.",
    "tool_result: ruff: 2 errors fixed, 0 remaining",
    "Ok, go ahead.",
]
_ANSWERS = [
    "The test is flaky because the writer task races the shutdown drain; awaiting the task "
    "before closing the pool fixes it.",
    "Done. The batch writer now uses COPY into a staging table, then one INSERT ... SELECT.",
    "The regex matches 32 lowercase hex characters, i.e. a W3C trace id.",
    "Added type hints; mypy --strict passes.",
    "The migration adds traces_time_idx on (start_time DESC, trace_id DESC).",
    "CI failed on a stale generated file; I regenerated it and the job is green.",
    "This branch adds facets, a time window on the traces list and a GIN index on models.",
]
_CODE_LINES = [
    "async def insert_spans(self, spans: Iterable[Span]) -> int:",
    "    records = [_record(s) for s in spans]",
    "    if not records:",
    "        return 0",
    "    async with self._pool.acquire() as conn, conn.transaction():",
    "        await conn.execute(_CREATE_STAGE)",
    '        await conn.copy_records_to_table("spans_stage", records=records)',
    "        inserted = await conn.fetchval(_MERGE_STAGE)",
    "    return int(inserted)",
    "",
    "def _cursor(row: asyncpg.Record) -> Cursor:",
    '    return Cursor(row["start_time"], row["trace_id"])',
]
_HISTORY_LINES = [
    "customer: I have been waiting for two weeks and nobody answers my emails.",
    "agent: I'm sorry about the wait. Let me check the order details for you.",
    "customer: The box was crushed and the lid of the kettle is cracked.",
    "agent: Thanks for the photos. I can see the damage clearly.",
    "customer: I also want to know why I was charged for express shipping.",
    "agent: Express shipping was selected at checkout; I can refund the difference.",
    "customer: Can you send the replacement to my office address instead?",
    "agent: Of course. Please confirm the address and I'll update the order.",
]


def _previews(inp: str | None = None, out: str | None = None) -> Attributes:
    """Input/output preview attributes, cut at PREVIEW_MAX_CHARS and flagged like the SDK."""
    attrs: Attributes = {}
    for text, key, flag in (
        (inp, Attr.INPUT_PREVIEW, Attr.INPUT_TRUNCATED),
        (out, Attr.OUTPUT_PREVIEW, Attr.OUTPUT_TRUNCATED),
    ):
        if text is None:
            continue
        attrs[key] = text[:PREVIEW_MAX_CHARS]
        if len(text) > PREVIEW_MAX_CHARS:
            attrs[flag] = True
    return attrs


def _text_rng(trace_id: str) -> random.Random:
    """Per-trace RNG for preview text, so text never perturbs the main sample stream."""
    return random.Random(int(trace_id, 16))  # noqa: S311 - sample data


def _long_text(trng: random.Random, header: str, lines: list[str]) -> str:
    """A text just over the preview limit (it gets truncated)."""
    out = [header]
    while sum(len(line) + 1 for line in out) <= PREVIEW_MAX_CHARS + 300:
        out.append(trng.choice(lines))
    return "\n".join(out)


@dataclass
class _Ids:
    rng: random.Random

    def trace(self) -> str:
        return f"{self.rng.getrandbits(128) or 1:032x}"

    def span(self) -> str:
        return f"{self.rng.getrandbits(64) or 1:016x}"


@dataclass
class _Builder:
    """Accumulates the spans of one trace."""

    ids: _Ids
    trace_id: str
    source: SpanSource
    spans: list[Span] = field(default_factory=list)

    def add(
        self,
        *,
        name: str,
        kind: SpanKind,
        start: datetime,
        end: datetime,
        parent: str | None,
        attributes: Attributes,
        status: SpanStatus = "ok",
        status_message: str | None = None,
        events: list[SpanEvent] | None = None,
        span_id: str | None = None,
    ) -> Span:
        span = Span(
            trace_id=self.trace_id,
            span_id=span_id or self.ids.span(),
            parent_span_id=parent,
            name=name,
            kind=kind,
            source=self.source,
            start_time=start,
            end_time=end,
            status=status,
            status_message=status_message,
            attributes=attributes,
            events=events or [],
        )
        self.spans.append(span)
        return span


def _ms(value: float) -> timedelta:
    return timedelta(milliseconds=value)


def _llm_attrs(
    *,
    system: str,
    request_model: str,
    response_model: str,
    input_tokens: int,
    output_tokens: int,
    finish: str,
    service: str,
    client: str,
    streaming: bool,
) -> Attributes:
    cost = cost_usd(response_model, input_tokens, output_tokens)
    attrs: Attributes = {
        Attr.SERVICE_NAME: service,
        Attr.CLIENT: client,
        Attr.GEN_AI_SYSTEM: system,
        Attr.GEN_AI_OPERATION: "chat",
        Attr.GEN_AI_REQUEST_MODEL: request_model,
        Attr.GEN_AI_RESPONSE_MODEL: response_model,
        Attr.GEN_AI_INPUT_TOKENS: input_tokens,
        Attr.GEN_AI_OUTPUT_TOKENS: output_tokens,
        Attr.GEN_AI_FINISH_REASONS: [finish],
        Attr.STREAMING: streaming,
    }
    if cost is not None:
        attrs[Attr.COST_USD] = cost
    return attrs


def _pick_start(rng: random.Random, now: datetime) -> datetime:
    """A start time in the last week, weighted towards weekday working hours (UTC)."""
    while True:
        start = now - WINDOW * rng.random()
        weight = 1.0 if 13 <= start.hour <= 23 else 0.25  # ~US working hours
        if start.weekday() >= 5:
            weight *= 0.35
        if rng.random() < weight:
            return start


# --------------------------------------------------------------------------- support agent


def _support_run(rng: random.Random, ids: _Ids, start: datetime, variant: str) -> list[Span]:
    b = _Builder(ids, ids.trace(), "sdk")
    trng = _text_rng(b.trace_id)
    root_id = ids.span()
    order = rng.randint(1000, 1999)
    session = f"sess_{rng.getrandbits(40):010x}"
    redacted = rng.random() < 0.3
    common: Attributes = {Attr.SERVICE_NAME: SUPPORT_SERVICE, Attr.CLIENT: "sdk"}

    questions = (
        _STATUS_QUESTIONS
        if variant in ("status", "not_found")
        else _RESCHEDULE_QUESTIONS
        if variant == "reschedule"
        else _QUESTIONS
    )
    question = rng.choice(questions).format(order=order)
    if redacted:
        question += " You can reach me at [REDACTED:email]."

    t = start + _ms(rng.uniform(2, 15))
    context = rng.randint(1400, 2200)  # system prompt + tool schemas + user message
    spent = 0.0
    budget_limit: float | None = None
    llm_index = 0
    root_status: SpanStatus = "ok"
    root_message: str | None = None

    shipped = trng.choice(["delivered", "in transit", "processing"])
    if variant == "reschedule":
        shipped = "processing"  # only an order that hasn't shipped can be rescheduled
    total = trng.choice([19, 24, 35, 49, 59, 89, 120, 149, 180, 250, 480])
    lookup_result = (
        f'lookup_order result: {{"order": {order}, "status": "{shipped}", '
        f'"total": {total}.00, "items": {trng.randint(1, 4)}}}'
    )
    eligible = f"Order {order} qualifies for a refund. Issuing it now."

    def llm(
        out_range: tuple[int, int],
        finish: str,
        *,
        final: bool = False,
        prompt: str,
        answer: str | None,
    ) -> None:
        nonlocal t, context, spent, llm_index, root_status, root_message
        llm_index += 1
        in_tok = context
        out_tok = rng.randint(*out_range)
        latency = 350 + out_tok * rng.uniform(9, 16) + in_tok * rng.uniform(0.02, 0.05)
        events: list[SpanEvent] = []
        response_model = SUPPORT_MODEL
        status: SpanStatus = "ok"
        message: str | None = None
        attrs_extra: Attributes = {}
        if variant == "failover" and llm_index == 2:
            failed_ms = rng.uniform(150, 400)
            retry_ms = rng.uniform(600, 1200)
            events.append(
                SpanEvent(
                    name=EventName.FAILOVER,
                    time=t + _ms(failed_ms + retry_ms),
                    attributes={
                        Attr.FAILOVER_FROM_MODEL: SUPPORT_MODEL,
                        Attr.FAILOVER_TO_MODEL: SUPPORT_FALLBACK,
                        Attr.FAILOVER_STATUS_CODE: 429,
                        Attr.FAILOVER_RETRIES: 1,
                    },
                )
            )
            latency = latency * 0.6 + failed_ms + retry_ms
            response_model = SUPPORT_FALLBACK
        if variant == "llm_error" and final:
            status, message = "error", "anthropic: 529 overloaded_error (after 2 retries)"
            out_tok = 0
            latency = rng.uniform(2500, 6000)
            finish = "error"
            root_status, root_message = "error", "model call failed"
        attrs_extra.update(_previews(prompt, None if status == "error" else answer))
        end = t + _ms(latency)
        attrs = _llm_attrs(
            system="anthropic",
            request_model=SUPPORT_MODEL,
            response_model=response_model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            finish=finish,
            service=SUPPORT_SERVICE,
            client="sdk",
            streaming=False,
        )
        attrs.update(attrs_extra)
        cost = attrs.get(Attr.COST_USD)
        spent += float(cost) if isinstance(cost, int | float) else 0.0
        if variant == "budget" and final and budget_limit is not None and spent > budget_limit:
            events.append(
                SpanEvent(
                    name=EventName.BUDGET_ALERT,
                    time=end,
                    attributes={
                        Attr.BUDGET_LIMIT_USD: budget_limit,
                        Attr.BUDGET_SPENT_USD: round(spent, 6),
                    },
                )
            )
        if llm_index == 1 and redacted:
            events.append(
                SpanEvent(
                    name=EventName.REDACTION,
                    time=t,
                    attributes={Attr.REDACTION_KIND: "email", Attr.REDACTION_COUNT: 1},
                )
            )
        b.add(
            name=f"chat {response_model}",
            kind="llm",
            start=t,
            end=end,
            parent=root_id,
            attributes=attrs,
            status=status,
            status_message=message,
            events=events,
        )
        context += out_tok
        t = end + _ms(rng.uniform(1, 6))

    def tool(
        name: str,
        latency: tuple[float, float],
        result_tokens: int,
        *,
        status: SpanStatus = "ok",
        message: str | None = None,
        extra: Attributes | None = None,
    ) -> None:
        nonlocal t, context
        end = t + _ms(rng.uniform(*latency))
        attrs: Attributes = {**common, Attr.GEN_AI_OPERATION: "execute_tool"}
        attrs[Attr.GEN_AI_TOOL_NAME] = name
        attrs.update(extra or {})
        b.add(
            name=name,
            kind="tool",
            start=t,
            end=end,
            parent=root_id,
            attributes=attrs,
            status=status,
            status_message=message,
        )
        context += result_tokens
        t = end + _ms(rng.uniform(1, 6))

    if variant == "budget":
        # Long conversations: the per-run limit (set before the last call) is crossed by it.
        context = rng.randint(9000, 14000)

    final_answer: str | None = None
    llm((60, 140), "tool_use", prompt=question, answer=f"Let me look up order {order}.")
    if variant == "not_found":
        tool("lookup_order", (15, 60), 40, status="error", message=f"order {order} not found")
        final_answer = (
            f"I couldn't find order {order}. Could you double-check the number in your "
            "confirmation email?"
        )
        llm(
            (80, 180),
            "end_turn",
            final=True,
            prompt=f"lookup_order error: order {order} not found",
            answer=final_answer,
        )
    elif variant == "llm_error":
        tool("lookup_order", (15, 60), rng.randint(180, 320))
        llm((0, 0), "error", final=True, prompt=lookup_result, answer=None)
    else:
        tool("lookup_order", (15, 60), rng.randint(180, 320))
        if variant == "status":
            final_answer = {
                "delivered": f"Order {order} was delivered yesterday; it was left at the door.",
                "in transit": f"Order {order} is in transit and should arrive within 2 days.",
                "processing": f"Order {order} is being packed and ships tomorrow.",
            }[shipped]
            llm((90, 220), "end_turn", final=True, prompt=lookup_result, answer=final_answer)
        elif variant == "reschedule":
            llm(
                (50, 110),
                "tool_use",
                prompt=lookup_result,
                answer=f"Order {order} hasn't shipped yet, so the delivery can still move. "
                "Rescheduling it for tomorrow at 10:00.",
            )
            tool("reschedule_delivery", (30, 120), 80)
            final_answer = (
                f"Order {order} hasn't shipped yet, so I've rescheduled the delivery for "
                "tomorrow at 10:00 am. You'll get a text when it's on the way."
            )
            llm(
                (90, 200),
                "end_turn",
                final=True,
                prompt=f'reschedule_delivery result: {{"order": {order}, '
                '"window": "tomorrow 10:00-10:30", "ok": true}',
                answer=final_answer,
            )
        elif variant == "guardrail":
            llm((60, 120), "tool_use", prompt=lookup_result, answer=eligible)
            amount = rng.choice([250, 320, 480, 750, 1200])
            g_start = t
            g_end = t + _ms(rng.uniform(1, 4))
            rule = f"refund_limit_{REFUND_LIMIT_USD}"
            b.add(
                name="guardrail refund_limit",
                kind="guardrail",
                start=g_start,
                end=g_end,
                parent=root_id,
                attributes={**common, Attr.GUARDRAIL_RULE: rule, Attr.REFUND_AMOUNT: amount},
                status="blocked",
                status_message=f"refund of ${amount} exceeds the ${REFUND_LIMIT_USD} limit",
                events=[
                    SpanEvent(
                        name=EventName.GUARDRAIL_BLOCK,
                        time=g_start,
                        attributes={Attr.GUARDRAIL_RULE: rule},
                    )
                ],
            )
            t = g_end + _ms(rng.uniform(1, 5))
            context += 60
            final_answer = (
                f"I can't approve a ${amount} refund automatically (the limit is "
                f"${REFUND_LIMIT_USD}), so I've passed order {order} to a colleague who will "
                "reply within one business day."
            )
            llm(
                (120, 260),
                "end_turn",
                final=True,
                prompt=f"issue_refund blocked by guardrail {rule}: ${amount} exceeds the limit",
                answer=final_answer,
            )
        else:
            llm((60, 120), "tool_use", prompt=lookup_result, answer=eligible)
            amount = rng.choice([19, 24, 35, 49, 59, 89, 120, 149, 180])
            tool("issue_refund", (40, 160), 60, extra={Attr.REFUND_AMOUNT: amount})
            tool("draft_email", (8, 30), rng.randint(150, 260))
            if variant == "budget":
                prices = cost_usd(SUPPORT_MODEL, context, 250) or 0.0
                budget_limit = round(spent + prices * 0.5, 4)
            final_answer = (
                f"Hi! I'm sorry about order {order}. I've issued a refund of ${amount}; it "
                "will show on your card in 3-5 business days. A confirmation email is on its way."
            )
            prompt = (
                _long_text(trng, f"Conversation so far (order {order}):", _HISTORY_LINES)
                if variant == "budget"
                else f"draft_email result: confirmation for a ${amount} refund drafted"
            )
            llm((150, 400), "end_turn", final=True, prompt=prompt, answer=final_answer)

    b.add(
        name="support-agent.run",
        kind="agent",
        start=start,
        end=t,
        parent=None,
        attributes={**common, Attr.SESSION_ID: session, **_previews(question, final_answer)},
        status=root_status,
        status_message=root_message,
        span_id=root_id,
    )
    return b.spans


# --------------------------------------------------------------------------- gateway sessions


def _gateway_session(rng: random.Random, ids: _Ids, start: datetime, client: str) -> list[Span]:
    b = _Builder(ids, ids.trace(), "gateway")
    trng = _text_rng(b.trace_id)
    root_id = ids.span()
    session = f"{client}-{rng.getrandbits(48):012x}"
    if client == "claude-code":
        system, turns = "anthropic", rng.randint(4, 18)
        main_model = rng.choices(["claude-sonnet-5", "claude-opus-5-5"], weights=[3, 1])[0]
        context = rng.randint(14000, 26000)
    elif client == "copilot-chat":
        system, turns = "openai", rng.randint(2, 9)
        main_model = rng.choices(["gpt-5", "gpt-5-mini"], weights=[2, 1])[0]
        context = rng.randint(3000, 9000)
    else:
        system, turns = "openai", rng.randint(1, 5)
        main_model = rng.choice(["gpt-5", "gpt-5-mini"])
        context = rng.randint(2000, 5000)
    common: Attributes = {
        Attr.SERVICE_NAME: GATEWAY_SERVICE,
        Attr.CLIENT: client,
        Attr.SESSION_ID: session,
    }

    t = start
    root_status: SpanStatus = "ok"
    first_prompt: str | None = None
    last_answer: str | None = None
    for turn in range(turns):
        if turn:
            t += timedelta(seconds=rng.uniform(4, 150))  # user think time / tool execution
        # Claude Code sends small side requests (titles, summaries) to Haiku.
        side = client == "claude-code" and rng.random() < 0.2
        model = "claude-haiku-4-5" if side else main_model
        in_tok = rng.randint(300, 900) if side else context
        out_tok = rng.randint(10, 60) if side else int(rng.lognormvariate(6.2, 0.8))
        out_tok = max(12, min(out_tok, 8000))
        ttft = rng.uniform(300, 1400) + in_tok * 0.004
        latency = ttft + out_tok * rng.uniform(8, 20)
        status: SpanStatus = "ok"
        message: str | None = None
        finish = "tool_use" if system == "anthropic" and rng.random() < 0.6 else "end_turn"
        if system == "openai":
            finish = "tool_calls" if rng.random() < 0.4 else "stop"
        if rng.random() < 0.03:
            status = "error"
            root_status = "error"
            code = 529 if system == "anthropic" else 503
            message = f"{system}: upstream {code} during stream"
            out_tok = rng.randint(0, out_tok // 3)
            latency = ttft + out_tok * 12
            finish = "error"
        attrs = _llm_attrs(
            system=system,
            request_model=model,
            response_model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            finish=finish,
            service=GATEWAY_SERVICE,
            client=client,
            streaming=True,
        )
        attrs[Attr.SESSION_ID] = session
        prompt: str
        if turn == 0:
            prompt = first_prompt = rng.choice(_CODING_PROMPTS)
        elif client == "claude-code" and trng.random() < 0.2:
            prompt = _long_text(
                trng, "tool_result: cat server/lucentpad_server/store.py", _CODE_LINES
            )
        else:
            prompt = trng.choice(_FOLLOW_UPS)
        answer: str
        if side:
            prompt = "Summarise this conversation as a title of at most 6 words."
            answer = trng.choice(["Fix flaky writer test", "Add COPY batch insert", "CI fix"])
        elif finish in ("tool_use", "tool_calls"):
            answer = f"I'll {trng.choice(_TOOL_ACTIONS)}."
        elif trng.random() < 0.15:
            answer = _long_text(trng, "Here is the updated function:", _CODE_LINES)
        else:
            answer = trng.choice(_ANSWERS)
        if status == "error":
            answer = answer[: max(1, len(answer) // 3)]
        attrs.update(_previews(prompt, answer))
        if not side:
            last_answer = answer
        end = t + _ms(latency)
        op = "messages" if system == "anthropic" else "chat.completions"
        b.add(
            name=f"{op} {model}",
            kind="llm",
            start=t,
            end=end,
            parent=root_id,
            attributes=attrs,
            status=status,
            status_message=message,
        )
        if not side:
            context += out_tok + rng.randint(200, 4000)  # tool results, file reads
        t = end

    b.add(
        name=f"{client} session",
        kind="agent",
        start=start,
        end=t,
        parent=None,
        attributes={**common, Attr.STREAMING: True, **_previews(first_prompt, last_answer)},
        status=root_status,
        span_id=root_id,
    )
    return b.spans


# --------------------------------------------------------------------------- public API


def generate(now: datetime, seed: int = 42) -> list[Span]:
    """About a week of traffic ending at ``now``, ordered by start_time. Deterministic."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    rng = random.Random(seed)  # noqa: S311 - sample data, not crypto
    ids = _Ids(random.Random(seed ^ 0x5EED))  # noqa: S311
    plan: list[tuple[str, str]] = [
        ("sdk", name) for name, n in _SUPPORT_VARIANTS for _ in range(n)
    ] + [("gateway", name) for name, n in _GATEWAY_VARIANTS for _ in range(n)]
    rng.shuffle(plan)

    spans: list[Span] = []
    for kind, variant in plan:
        start = _pick_start(rng, now)
        trace = (
            _support_run(rng, ids, start, variant)
            if kind == "sdk"
            else _gateway_session(rng, ids, start, variant)
        )
        overshoot = max(s.end_time for s in trace) - now
        if overshoot > timedelta(0):
            trace = [_shift(s, -overshoot) for s in trace]
        spans.extend(trace)
    spans.sort(key=lambda s: (s.start_time, s.trace_id, s.span_id))
    return spans


def _shift(span: Span, delta: timedelta) -> Span:
    return span.model_copy(
        update={
            "start_time": span.start_time + delta,
            "end_time": span.end_time + delta,
            "events": [e.model_copy(update={"time": e.time + delta}) for e in span.events],
        }
    )


def to_json(spans: list[Span]) -> dict[str, Any]:
    return {"spans": [s.model_dump(mode="json") for s in spans]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Dump deterministic LucentPad sample spans (JSON)."
    )
    parser.add_argument("--out", type=Path, required=True, help="output file")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="end of the generated week, ISO 8601 with timezone (default: now, UTC)",
    )
    args = parser.parse_args(argv)
    now: datetime = args.now or datetime.now(UTC).replace(microsecond=0)
    spans = generate(now, seed=args.seed)
    args.out.write_text(json.dumps(to_json(spans), indent=2) + "\n")
    print(f"wrote {len(spans)} spans to {args.out}")


if __name__ == "__main__":
    main()
