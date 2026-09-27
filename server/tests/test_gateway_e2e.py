"""Gateway end to end: the real app (lifespan, ingest queue, writer, Postgres) with the gateway
pointed at a fake upstream. A streamed session becomes one trace readable via the query API,
with a cost on every llm span. No LLM API is called."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from lucentpad_server.app import create_app
from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.gateway.proxy import HttpGatewayProxy
from lucentpad_server.schema import Attr

from .gateway_fakes import (
    ANTHROPIC_STREAM,
    API_KEY,
    FakeStream,
    FakeUpstream,
    anthropic_headers,
    anthropic_request,
    chop,
)
from .support import app_client

READABLE_WITHIN_S = 2.0


def _fresh_stream(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=FakeStream(chop(ANTHROPIC_STREAM)),
    )


async def test_gateway_session_is_one_trace_with_costs(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LUCENTPAD_ANTHROPIC_UPSTREAM", "https://anthropic.test")
    app = create_app(db_url, seed_sample=False)
    upstream = FakeUpstream([_fresh_stream])
    async with app_client(app) as client:
        # The lifespan built the real gateway; swap its transport for the fake upstream.
        assert isinstance(app.state.gateway, HttpGatewayProxy)
        await app.state.gateway.aclose()
        gateway = HttpGatewayProxy(
            GatewayConfig.from_env(), app.state.ingest, transport=upstream.transport()
        )
        app.state.gateway = gateway

        for _ in range(2):
            r = await client.post(
                "/gateway/anthropic/v1/messages",
                content=anthropic_request(),
                headers=anthropic_headers(),
            )
            assert r.status_code == 200 and r.content == ANTHROPIC_STREAM
        sent_at = time.monotonic()
        await gateway.drain()

        detail: dict[str, object] = {}
        while time.monotonic() - sent_at < READABLE_WITHIN_S + 3:
            traces = (await client.get("/v1/traces", params={"source": "gateway"})).json()
            if traces["traces"]:
                trace_id = traces["traces"][0]["trace_id"]
                detail = (await client.get(f"/v1/traces/{trace_id}")).json()
                if len(detail["spans"]) == 3:  # type: ignore[arg-type]
                    break
            await asyncio.sleep(0.05)
        readable_after = time.monotonic() - sent_at
        assert readable_after < READABLE_WITHIN_S, readable_after

        trace = detail["trace"]
        spans = detail["spans"]
        assert isinstance(trace, dict) and isinstance(spans, list)
        assert trace["name"] == "claude-code session"
        assert trace["source"] == "gateway"
        assert trace["client"] == "claude-code"
        assert trace["service_name"] == "lucentpad-gateway"
        assert trace["llm_calls"] == 2
        assert trace["input_tokens"] == 2400 and trace["output_tokens"] == 84
        # claude-sonnet-5: $2 / $10 per MTok -> 1200*2e-6 + 42*10e-6 per call
        assert trace["cost_usd"] == pytest.approx(2 * (1200 * 2 + 42 * 10) / 1_000_000)
        llm = [s for s in spans if s["kind"] == "llm"]
        assert len(llm) == 2
        for s in llm:
            assert s["attributes"][Attr.COST_USD] == pytest.approx(0.00282)
            assert s["attributes"][Attr.KEY_FINGERPRINT]
            assert API_KEY not in str(s)
        assert trace["input_preview"] == "[tool_result Bash]\nnow read a.py"
        assert trace["output_preview"].endswith("[tool_use Read]")
        # the root starts the trace; the turns stretch its end
        assert trace["duration_ms"] > 0
