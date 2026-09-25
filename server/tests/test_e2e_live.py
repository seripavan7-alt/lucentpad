"""End to end (M1 step 7): the demo agent in ``--mock-llm`` mode exports through the real SDK to
the real API (uvicorn on a random port) over real Postgres. No LLM API is called.

Checks the PRD's M1 "done when": each span is queryable within 2 s of ending, the tree matches
the demo script, every llm span has a cost, and the trace carries the question and answer.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime

import httpx
import pytest
import uvicorn

import lucentpad
from lucentpad_server.app import create_app
from support_agent.__main__ import main as agent_main

QUESTION = "Where's order 1042? I want a refund."
LIVE_LIMIT_S = 2.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
def api_url(db_url: str) -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        create_app(db_url, seed_sample=False), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("API did not start")
    yield url
    server.should_exit = True
    thread.join(timeout=10)


def test_agent_run_draws_live(api_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    agent = threading.Thread(
        target=agent_main,
        args=([QUESTION, "--mock-llm", "--mock-delay", "0.3", "--endpoint", api_url],),
        daemon=True,
    )
    agent.start()

    # Poll like the dashboard does and note when each span first becomes readable.
    first_seen: dict[str, float] = {}
    spans: dict[str, dict[str, object]] = {}
    trace_id: str | None = None
    detail: dict[str, object] = {}
    deadline = time.monotonic() + 30
    with httpx.Client(base_url=api_url, timeout=2) as client:
        while time.monotonic() < deadline:
            if trace_id is None:
                traces = client.get("/v1/traces", params={"limit": 5}).json()["traces"]
                if traces:
                    trace_id = traces[0]["trace_id"]
            if trace_id is not None:
                detail = client.get(f"/v1/traces/{trace_id}").json()
                now = time.time()
                for s in detail["spans"]:  # type: ignore[attr-defined]
                    first_seen.setdefault(s["span_id"], now)
                    spans[s["span_id"]] = s
                done = any(s["parent_span_id"] is None for s in spans.values())
                if done and not agent.is_alive():
                    break
            time.sleep(0.05)
    agent.join(timeout=10)
    lucentpad.shutdown()
    assert trace_id is not None, "no trace appeared"

    # Tree: root → llm, lookup_order, llm, issue_refund, draft_email, llm (in time order).
    root = next(s for s in spans.values() if s["parent_span_id"] is None)
    children = sorted(
        (s for s in spans.values() if s["parent_span_id"] == root["span_id"]),
        key=lambda s: str(s["start_time"]),
    )
    kinds = [(s["kind"], s["name"] if s["kind"] == "tool" else "llm") for s in children]
    assert root["kind"] == "agent"
    assert kinds == [
        ("llm", "llm"),
        ("tool", "lookup_order"),
        ("llm", "llm"),
        ("tool", "issue_refund"),
        ("tool", "draft_email"),
        ("llm", "llm"),
    ]

    # Live: every span readable within 2 s of ending.
    for sid, s in spans.items():
        ended = datetime.fromisoformat(str(s["end_time"])).timestamp()
        lag = first_seen[sid] - ended
        assert lag <= LIVE_LIMIT_S, f"{s['name']} readable {lag:.2f}s after it ended"

    # Cost per call, and the question → answer previews on the trace summary.
    summary = detail["trace"]
    assert isinstance(summary, dict)
    assert summary["llm_calls"] == 3 and summary["cost_usd"] > 0
    llm_ids = [s["span_id"] for s in children if s["kind"] == "llm"]
    with httpx.Client(base_url=api_url) as client:
        stored = client.get(f"/v1/traces/{trace_id}").json()["spans"]
    costs = {s["span_id"]: s["attributes"].get("lucentpad.cost_usd") for s in stored}
    # The SDK never sends cost; the API prices tokens and returns it on every llm span.
    for llm_id in llm_ids:
        cost = costs[llm_id]
        assert isinstance(cost, float) and cost > 0, f"llm span {llm_id} has no cost"
    assert summary["input_preview"] == QUESTION
    answer = capsys.readouterr().out.strip()
    assert answer and summary["output_preview"] == answer
