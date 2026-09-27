"""Latency the gateway adds (PRD: < 10 ms at p95) against a local fake upstream.

Compares the same request made directly to the fake upstream with one made through the full
app (routing, proxy, side parsing, session lookup, span scheduling). The bound is the PRD
target, which is ~10x the typical measurement, so the test stays stable on a busy CI runner;
the measured numbers are printed (``pytest -s``) and attached as ``record_property``.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Awaitable, Callable

import httpx
import pytest

from .gateway_fakes import (
    ANTHROPIC_STREAM,
    FakeStream,
    FakeUpstream,
    anthropic_headers,
    anthropic_request,
    asgi_client,
    chop,
    make_gateway,
)

N = 300
WARMUP = 30
P95_BOUND_MS = 10.0


def _stream(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=FakeStream(chop(ANTHROPIC_STREAM, (256,))),
    )


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[18]


async def _measure(call: Callable[[], Awaitable[bytes]]) -> list[float]:
    out: list[float] = []
    for i in range(N + WARMUP):
        t = time.perf_counter()
        body = await call()
        if i >= WARMUP:
            out.append((time.perf_counter() - t) * 1000)
        assert body == ANTHROPIC_STREAM
    return out


async def test_gateway_added_p95_latency(record_property: Callable[[str, object], None]) -> None:
    upstream = FakeUpstream([_stream])
    app, proxy, _ = make_gateway(upstream)
    body = anthropic_request()
    headers = anthropic_headers()
    direct_client = httpx.AsyncClient(transport=upstream.transport())

    async def direct() -> bytes:
        async with direct_client.stream(
            "POST", "https://anthropic.test/v1/messages", content=body, headers=headers
        ) as r:
            return b"".join([c async for c in r.aiter_raw()])

    async with asgi_client(app) as gw:

        async def via_gateway() -> bytes:
            r = await gw.post("/gateway/anthropic/v1/messages", content=body, headers=headers)
            return r.content

        direct_ms = await _measure(direct)
        gateway_ms = await _measure(via_gateway)
    await proxy.drain()
    await direct_client.aclose()
    await proxy.aclose()

    added = _p95(gateway_ms) - _p95(direct_ms)
    record_property("gateway_p95_ms", round(_p95(gateway_ms), 3))
    record_property("direct_p95_ms", round(_p95(direct_ms), 3))
    record_property("added_p95_ms", round(added, 3))
    print(
        f"\ngateway p95 {_p95(gateway_ms):.2f} ms, direct p95 {_p95(direct_ms):.2f} ms, "
        f"added p95 {added:.2f} ms (median added "
        f"{statistics.median(gateway_ms) - statistics.median(direct_ms):.2f} ms)"
    )
    if added >= P95_BOUND_MS:
        pytest.fail(f"gateway adds {added:.2f} ms at p95 (target < {P95_BOUND_MS} ms)")
