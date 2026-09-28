"""Redaction at ingest (M3 step 3, D20 last line): secrets in any stored string are replaced and
recorded as one ``lucentpad.redaction`` event per kind; the values never reach the database."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from lucentpad_server.app import create_app
from lucentpad_server.guardrails import redaction
from lucentpad_server.guardrails.redaction import redact_span, redact_spans
from lucentpad_server.schema import PREVIEW_MAX_CHARS, Attr, EventName, Span, SpanEvent

from .support import app_client

EMAIL = "maya.patel@example.com"
KEY = "sk-ant-api03-Abcdefghijklmnopqrstuvwxyz012345"
CARD = "4111 1111 1111 1111"
SECRETS = (EMAIL, KEY, CARD, "4111111111111111", "Abcdefghijklmnopqrstuvwxyz012345")
T0 = datetime(2026, 9, 25, 12, tzinfo=UTC)

_n = 0


def _span(**kw: Any) -> Span:
    global _n
    _n += 1
    data: dict[str, Any] = {
        "trace_id": f"{0xABC000 + _n:032x}",
        "span_id": f"{0xDEF000 + _n:016x}",
        "name": "chat claude-sonnet-5",
        "kind": "llm",
        "source": "sdk",
        "start_time": T0,
        "end_time": T0 + timedelta(seconds=1),
        **kw,
    }
    return Span.model_validate(data)


def _redaction_events(span: Span) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in span.events:
        if e.name == EventName.REDACTION:
            kind = e.attributes[Attr.REDACTION_KIND]
            count = e.attributes[Attr.REDACTION_COUNT]
            assert isinstance(kind, str) and isinstance(count, int)
            assert kind not in out, "one event per kind"
            out[kind] = count
    return out


# --------------------------------------------------------------------------- unit


def test_redacts_every_string_and_records_counts() -> None:
    span = _span(
        status="error",
        status_message=f"refund for {EMAIL} failed",
        attributes={
            Attr.INPUT_PREVIEW: f"I'm {EMAIL}, card {CARD}, my key is {KEY}. cc {EMAIL}",
            Attr.OUTPUT_PREVIEW: "Thanks! I'll email you.",
            "app.notes": [f"call {EMAIL}", "nothing here"],
            Attr.GEN_AI_INPUT_TOKENS: 1200,
            Attr.GEN_AI_REQUEST_MODEL: "claude-sonnet-5",
        },
        events=[SpanEvent(name="app.note", time=T0, attributes={"who": EMAIL})],
    )
    out = redact_span(span)
    text = out.model_dump_json()
    for secret in SECRETS:
        assert secret not in text
    a = out.attributes
    assert a[Attr.INPUT_PREVIEW] == (
        "I'm [REDACTED:email], card [REDACTED:card], my key is [REDACTED:api_key]. "
        "cc [REDACTED:email]"
    )
    assert a[Attr.OUTPUT_PREVIEW] == "Thanks! I'll email you."
    assert a["app.notes"] == ["call [REDACTED:email]", "nothing here"]
    assert a[Attr.GEN_AI_INPUT_TOKENS] == 1200
    assert out.status_message == "refund for [REDACTED:email] failed"
    assert out.events[0].attributes == {"who": "[REDACTED:email]"}
    # email: 2 in the input, 1 in the list, 1 in the message, 1 in the event.
    assert _redaction_events(out) == {"api_key": 1, "card": 1, "email": 5}
    red = [e for e in out.events if e.name == EventName.REDACTION]
    assert all(e.time == span.start_time for e in red)
    Span.model_validate(out.model_dump())  # still a valid span


def test_clean_span_is_returned_unchanged() -> None:
    span = _span(attributes={Attr.INPUT_PREVIEW: "Where is order 1042? Trace 0af7651916cd43dd"})
    assert redact_span(span) is span


def test_existing_event_is_not_duplicated() -> None:
    sdk_event = SpanEvent(
        name=EventName.REDACTION,
        time=T0,
        attributes={Attr.REDACTION_KIND: "email", Attr.REDACTION_COUNT: 1},
    )
    span = _span(
        attributes={Attr.INPUT_PREVIEW: f"mail {EMAIL} key {KEY}"},
        events=[sdk_event],
    )
    out = redact_span(span)
    assert EMAIL not in out.model_dump_json() and KEY not in out.model_dump_json()
    # The SDK's email event stays as it was; only the new kind gets an event.
    assert _redaction_events(out) == {"email": 1, "api_key": 1}
    # Idempotent: a second pass changes nothing.
    assert redact_span(out) is out


def test_long_message_stays_within_limit() -> None:
    span = _span(status="error", status_message=("a@b.co " * 285)[:2000])
    out = redact_span(span)
    assert out.status_message is not None and len(out.status_message) <= 2000
    Span.model_validate(out.model_dump())


def test_failure_drops_previews_not_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_text: str) -> object:
        raise RuntimeError("engine bug")

    monkeypatch.setattr(redaction, "redact", boom)
    span = _span(attributes={Attr.INPUT_PREVIEW: EMAIL, Attr.GEN_AI_INPUT_TOKENS: 5})
    (out,) = redact_spans([span])
    assert Attr.INPUT_PREVIEW not in out.attributes
    assert out.attributes[Attr.GEN_AI_INPUT_TOKENS] == 5


def test_redaction_cost_per_span() -> None:
    """Ingest-side cost: realistic llm spans with 2 kB previews (a fifth with an email)."""
    filler = "The customer asked about order 1042 and the delivery window. " * 40
    spans = [
        _span(
            attributes={
                Attr.SERVICE_NAME: "support-agent",
                Attr.CLIENT: "sdk",
                Attr.GEN_AI_SYSTEM: "anthropic",
                Attr.GEN_AI_REQUEST_MODEL: "claude-sonnet-5",
                Attr.GEN_AI_INPUT_TOKENS: 1500,
                Attr.GEN_AI_OUTPUT_TOKENS: 120,
                Attr.INPUT_PREVIEW: ((f"reach me at {EMAIL}. " if i % 5 == 0 else "") + filler)[
                    :PREVIEW_MAX_CHARS
                ],
                Attr.OUTPUT_PREVIEW: filler[:PREVIEW_MAX_CHARS],
            }
        )
        for i in range(1000)
    ]
    redact_spans(spans[:50])  # warm up regex caches
    t = time.perf_counter()
    out = redact_spans(spans)
    per_span_us = (time.perf_counter() - t) / len(spans) * 1e6
    print(f"\nredaction at ingest: {per_span_us:.1f} us/span (2 x 2 kB previews)")
    assert sum(1 for s in out if _redaction_events(s)) == 200
    assert per_span_us < 2000  # generous: CI machines vary; typical is far lower


# --------------------------------------------------------------------------- ingest (real DB)


async def _settle(client: Any, wait: float = 5.0) -> None:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        stats = (await client.get("/v1/ingest/stats")).json()
        if stats["queue_depth"] == 0 and stats["written_total"] + stats["write_errors_total"] > 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("ingest did not settle")


async def test_ingest_never_stores_secrets(db_url: str) -> None:
    root = _span(
        name="support-agent.run",
        kind="agent",
        attributes={Attr.INPUT_PREVIEW: f"Refund order 1057 please, I'm {EMAIL}"},
    )
    child = _span(
        trace_id=root.trace_id,
        parent_span_id=root.span_id,
        status="error",
        status_message=f"upstream rejected key {KEY}",
        attributes={
            Attr.INPUT_PREVIEW: f"pay with {CARD}",
            Attr.OUTPUT_PREVIEW: f"Sure, {EMAIL}",
            Attr.GEN_AI_REQUEST_MODEL: "claude-sonnet-5",
            Attr.GEN_AI_INPUT_TOKENS: 100,
            Attr.GEN_AI_OUTPUT_TOKENS: 10,
        },
    )
    body = {"spans": [s.model_dump(mode="json") for s in (root, child)]}
    async with app_client(create_app(db_url, seed_sample=False)) as client:
        assert (await client.post("/v1/spans", json=body)).status_code == 202
        await _settle(client)
        # Re-sent (SDK retry): stored once, events not doubled.
        assert (await client.post("/v1/spans", json=body)).status_code == 202
        await asyncio.sleep(0.3)
        detail = (await client.get(f"/v1/traces/{root.trace_id}")).json()
        events = (await client.get("/v1/guardrails/events", params={"kind": "redaction"})).json()

    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch("SELECT row_to_json(s)::text AS j FROM spans s")
        traces = await conn.fetch("SELECT row_to_json(t)::text AS j FROM traces t")
    finally:
        await conn.close()
    stored = "\n".join(r["j"] for r in [*rows, *traces])
    assert len(rows) == 2
    for secret in SECRETS:
        assert secret not in stored, "a secret reached the database"
    assert "[REDACTED:email]" in stored

    spans = {s["span_id"]: Span.model_validate(s) for s in detail["spans"]}
    assert _redaction_events(spans[root.span_id]) == {"email": 1}
    assert _redaction_events(spans[child.span_id]) == {"api_key": 1, "card": 1, "email": 1}
    assert detail["trace"]["input_preview"] == "Refund order 1057 please, I'm [REDACTED:email]"
    assert spans[child.span_id].status_message == "upstream rejected key [REDACTED:api_key]"
    got = sorted((e["span_id"] == child.span_id, e["redaction_kind"]) for e in events["events"])
    assert got == [(False, "email"), (True, "api_key"), (True, "card"), (True, "email")]
    assert json.dumps(events).count("[REDACTED") == 0  # events carry kinds, never values
