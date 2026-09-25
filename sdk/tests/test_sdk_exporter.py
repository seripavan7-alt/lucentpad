"""Exporter: bounded buffer (drop oldest), batching, backoff on 429/5xx/network, never blocks."""

from __future__ import annotations

import threading
import time
from typing import Any

import anthropic
import httpx
import httpx2
from conftest import FAKE_KEY, Ingest, Provider, anthropic_message, json_response, start_sdk

import lucentpad
from lucentpad._core import SpanRecord, new_trace_id
from lucentpad._exporter import Exporter


def span_dict(i: int = 0) -> dict[str, Any]:
    rec = SpanRecord(name=f"s{i}", kind="tool", trace_id=new_trace_id(), parent=None)
    rec.end_time = rec.start_time
    return rec.to_dict()


def make(handler: Any, **kwargs: Any) -> Exporter:
    kwargs.setdefault("interval", 0.01)
    return Exporter("http://ingest.test", transport=httpx.MockTransport(handler), **kwargs)


def test_full_buffer_drops_oldest_and_counts() -> None:
    exp = make(lambda r: httpx.Response(202), max_buffer=3, start=False)
    for i in range(5):
        exp.submit(span_dict(i))
    assert exp.buffered == 3
    assert exp.dropped == 2
    assert [s["name"] for s in exp._buf] == ["s2", "s3", "s4"]
    exp.shutdown(0.1)


def test_batches_of_at_most_100() -> None:
    ingest = Ingest()
    exp = make(ingest.handler, interval=5.0)
    for i in range(250):
        exp.submit(span_dict(i))
    assert exp.flush(3.0)
    sizes = [len(httpx.Response(200, content=r.content).json()["spans"]) for r in ingest.requests]
    assert sum(sizes) == 250 and max(sizes) <= 100 and len(sizes) >= 3
    assert [s["name"] for s in ingest.spans] == [f"s{i}" for i in range(250)]  # order kept
    assert exp.sent == 250
    exp.shutdown()
    assert not ingest.invalid


def test_429_honours_retry_after_and_keeps_spans() -> None:
    ingest = Ingest(status=429, headers={"Retry-After": "0.3"})
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(time.monotonic())
        response = ingest.handler(request)
        ingest.status = 202  # recover after the first refusal
        return response

    exp = make(handler)
    exp.submit(span_dict(1))
    exp.submit(span_dict(2))
    deadline = time.monotonic() + 3
    while len(ingest.spans) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [s["name"] for s in ingest.spans] == ["s1", "s2"]
    assert times[1] - times[0] >= 0.28
    assert exp.failures == 1 and exp.dropped == 0
    exp.shutdown()


def test_5xx_is_retried_with_backoff() -> None:
    calls = {"n": 0}
    ingest = Ingest()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503)
        return ingest.handler(request)

    exp = make(handler, max_backoff=0.05)
    exp.submit(span_dict())
    deadline = time.monotonic() + 3
    while not ingest.spans and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(ingest.spans) == 1 and calls["n"] == 3
    exp.shutdown()


def test_413_splits_the_batch_and_other_4xx_drop() -> None:
    ingest = Ingest()

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(httpx.Response(200, content=request.content).json()["spans"])
        if n > 2:
            return httpx.Response(413)
        if b'"s9"' in request.content:
            return httpx.Response(422)
        return ingest.handler(request)

    exp = make(handler, interval=5.0, start=False)
    for i in range(8):
        exp.submit(span_dict(i))
    exp.submit(span_dict(9))
    exp._thread.start()
    assert exp.flush(3.0) is False  # some spans were refused
    # halves until they fit: [s0 s1] [s2 s3] [s4 s5] [s6] [s7 s9]; the last one is refused (422)
    assert sorted(s["name"] for s in ingest.spans) == [f"s{i}" for i in range(7)]
    assert exp.rejected == 2
    exp.shutdown()


def test_failed_sends_stay_bounded() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    exp = make(down, max_buffer=5, max_backoff=0.01)
    for i in range(20):
        exp.submit(span_dict(i))
    time.sleep(0.2)
    assert exp.buffered <= 5
    assert exp.dropped >= 15
    assert exp.flush(0.2) is False
    exp.shutdown(0.1)


def test_ingest_down_leaves_the_host_unaffected() -> None:
    hang = threading.Event()

    def down(request: httpx.Request) -> httpx.Response:
        hang.wait(0.5)  # a slow, then failing, collector
        raise httpx.ConnectError("refused", request=request)

    exporter = Exporter("http://ingest.test", transport=httpx.MockTransport(down), interval=0.01)
    from lucentpad import _core

    _core.configure(
        "http://ingest.test",
        service_name="svc",
        capture_content=True,
        enabled=True,
        exporter=exporter,
    )
    provider = Provider([json_response(anthropic_message("fine"))] * 50)
    client = lucentpad.wrap(
        anthropic.Anthropic(
            api_key=FAKE_KEY,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(
                transport=httpx2.MockTransport(provider.handler)
            ),
        )
    )
    start = time.monotonic()
    for _ in range(50):
        with lucentpad.trace("run"):
            reply = client.messages.create(
                model="claude-sonnet-5", max_tokens=5, messages=[{"role": "user", "content": "q"}]
            )
            assert reply.content[0].type == "text"
    elapsed = time.monotonic() - start
    assert elapsed < 2.0  # 50 traced runs never waited on the 0.5 s collector
    t0 = time.monotonic()
    assert lucentpad.flush(0.2) is False
    assert time.monotonic() - t0 < 0.6
    hang.set()
    lucentpad.shutdown()


def test_submit_does_not_block_while_a_send_is_in_flight() -> None:
    release = threading.Event()
    ingest = Ingest()

    def slow(request: httpx.Request) -> httpx.Response:
        release.wait(2.0)
        return ingest.handler(request)

    exp = make(slow)
    exp.submit(span_dict(0))
    time.sleep(0.05)  # the thread is now stuck inside the POST
    t0 = time.monotonic()
    for i in range(1, 2001):
        exp.submit(span_dict(i))
    assert time.monotonic() - t0 < 0.5
    release.set()
    assert exp.flush(3.0)
    assert len(ingest.spans) == 2001
    exp.shutdown()


def test_flush_and_shutdown_are_safe_without_init() -> None:
    assert lucentpad.flush() is True
    lucentpad.shutdown()
    lucentpad.shutdown()


def test_spans_are_exported_as_they_end() -> None:
    ingest = Ingest()
    start_sdk(ingest)

    @lucentpad.span
    def step() -> None:
        pass

    with lucentpad.trace("run"):
        step()
        deadline = time.monotonic() + 2
        while not ingest.spans and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [s["name"] for s in ingest.spans] == ["step"]  # root still open
    assert lucentpad.flush(2.0)
    assert ingest.names() == ["step", "run"]
