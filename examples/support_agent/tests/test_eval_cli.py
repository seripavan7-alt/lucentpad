"""``lucentpad eval`` (sdk/lucentpad/cli) with a fake target and a fake LucentPad API, plus the
committed demo suite in mock mode. No network, no keys."""

from __future__ import annotations

import importlib
import json
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from agent_fake_api import FakeApi

from lucentpad.cli import _eval, main
from lucentpad.cli._checks import Observation, ToolCall, evaluate, tool_calls
from lucentpad.cli._prices import FALLBACK_PRICES, spans_cost
from lucentpad.cli._suite import SuiteError, load_suite, parse_check
from lucentpad_server import pricing
from lucentpad_server.schema import Attr, EvalRunIn
from support_agent import config

REPO = Path(__file__).resolve().parents[3]
ENDPOINT = "http://lucentpad.test:8000"


@pytest.fixture
def fake_api() -> FakeApi:
    return FakeApi()


TARGET = '''
"""A fake eval target: one traced model call (fixed token counts) and, for questions with an
order number, one lookup_order tool call. Tests steer it through STATE."""
import json
import re

import anthropic
import httpx2

import lucentpad

STATE = {"answer": "Order {order} has shipped.", "output_tokens": 40, "raise": False}


@lucentpad.span
def lookup_order(order_id):
    lucentpad.set_attribute("lucentpad.input.preview", json.dumps({"order_id": order_id}))
    return {"order_id": order_id}


def _reply(request):
    body = json.loads(request.content)
    return httpx2.Response(200, json={
        "id": "msg_fake", "type": "message", "role": "assistant", "model": body["model"],
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": STATE["output_tokens"]},
    })


def run(input, *, model=None, mock=False):
    if STATE["raise"]:
        raise RuntimeError("target exploded")
    client = lucentpad.wrap(anthropic.Anthropic(
        api_key="fake-not-a-key", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(_reply)),
    ))
    with lucentpad.trace("fake.run") as t:
        client.messages.create(model=model or "claude-haiku-4-5", max_tokens=10,
                               messages=[{"role": "user", "content": input}])
        m = re.search(r"\\d{4}", input)
        if m:
            lookup_order(order_id=m.group(0))
            return {"answer": STATE["answer"].format(order=m.group(0)), "trace_id": t.trace_id}
    return {"answer": "Hello! How can I help?", "trace_id": t.trace_id}


def no_mock(input, *, model=None):
    return {"answer": "x"}
'''

SUITE = """
suite: fake
target: fake_eval_target:run
model: claude-haiku-4-5
cases:
  - id: status
    input: "Where is order 1043?"
    checks:
      - tool_called: {name: lookup_order, args: {order_id: "1043"}}
      - contains: SHIPPED
      - max_cost_usd: 0.01
  - id: small_talk
    input: "hi"
    checks:
      - no_tool
      - max_latency_ms: 60000
"""

_cost = pricing.cost_usd("claude-haiku-4-5", 1000, 40)
assert _cost is not None
ONE_CALL_COST: float = _cost


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    (tmp_path / "fake_eval_target.py").write_text(TARGET, encoding="utf-8")
    (tmp_path / "suite.yaml").write_text(SUITE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for key in ("GITHUB_ACTIONS", "LUCENTPAD_ENDPOINT", "LUCENTPAD_DISABLED"):
        monkeypatch.delenv(key, raising=False)
    sys.modules.pop("fake_eval_target", None)
    yield importlib.import_module("fake_eval_target")
    sys.modules.pop("fake_eval_target", None)


def run(api: FakeApi, *args: str) -> int:
    return main(["eval", "suite.yaml", "--endpoint", ENDPOINT, *args], transport=api.transport())


def test_all_pass_reads_the_api_and_posts_a_valid_run(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake_api) == 0
    out, err = capsys.readouterr()
    assert "status  " in out and "pass" in out and "status passed" in out
    assert "from the LucentPad API (server-computed cost)" in out
    assert "no baseline at baseline.json" in out
    assert "run posted" in err

    (posted,) = fake_api.runs
    run_in = EvalRunIn.model_validate(posted)
    assert (
        run_in.suite == "fake" and run_in.status == "passed" and run_in.model == "claude-haiku-4-5"
    )
    assert [c.case for c in run_in.cases] == ["status", "small_talk"]
    status = run_in.cases[0]
    assert status.passed and status.baseline_passed is None
    assert status.cost_usd == ONE_CALL_COST  # server-computed, read back from the API
    assert run_in.cost_usd == pytest.approx(2 * ONE_CALL_COST)
    assert status.output_preview == "Order 1043 has shipped."
    assert [c.check for c in status.checks] == [
        'tool_called: lookup_order {"order_id": "1043"}',
        "contains: SHIPPED",
        "max_cost_usd: 0.01",
    ]

    # each case is its own trace; the root span carries the eval run id and case
    assert status.trace_id is not None
    spans = fake_api.trace_spans(status.trace_id)
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["name"] == "eval fake/status"
    assert root["attributes"][Attr.EVAL_CASE] == "status"
    assert len(root["attributes"][Attr.EVAL_RUN_ID]) == 32
    other = fake_api.trace_spans(run_in.cases[1].trace_id or "")
    other_root = next(s for s in other if s["parent_span_id"] is None)
    assert other_root["attributes"][Attr.EVAL_RUN_ID] == root["attributes"][Attr.EVAL_RUN_ID]


def test_update_baseline_writes_the_file(target: ModuleType, fake_api: FakeApi) -> None:
    target.STATE["answer"] = "no idea"  # status fails: still written, and exit 0
    assert run(fake_api, "--update-baseline", "--no-post") == 0
    body = json.loads(Path("baseline.json").read_text())
    assert body["suite"] == "fake" and body["model"] == "claude-haiku-4-5" and not body["mock"]
    assert body["cases"] == {
        "status": {"passed": False, "cost_usd": ONE_CALL_COST},
        "small_talk": {"passed": True, "cost_usd": ONE_CALL_COST},
    }
    assert body["total_cost_usd"] == pytest.approx(2 * ONE_CALL_COST)
    assert not fake_api.runs  # --no-post


def test_a_baseline_passing_case_that_fails_is_a_regression(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake_api, "--update-baseline", "--no-post") == 0
    target.STATE["answer"] = "I have no idea where order {order} is."
    capsys.readouterr()
    assert run(fake_api) == 1
    out = capsys.readouterr().out
    assert "REGRESSED" in out and "contains: SHIPPED (not in the answer)" in out
    assert "status regressed" in out
    run_in = EvalRunIn.model_validate(fake_api.runs[-1])
    assert run_in.status == "regressed"
    assert run_in.cases[0].baseline_passed is True and not run_in.cases[0].passed
    assert run_in.baseline_cost_usd == pytest.approx(2 * ONE_CALL_COST)


def test_a_case_failing_in_the_baseline_too_is_not_a_regression(
    target: ModuleType, fake_api: FakeApi
) -> None:
    target.STATE["answer"] = "no idea"
    assert run(fake_api, "--update-baseline", "--no-post") == 0
    assert run(fake_api, "--no-post") == 0  # reported as FAIL, but the gate stays green


def test_cost_rise_over_25_percent_is_a_regression(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake_api, "--update-baseline", "--no-post") == 0
    target.STATE["output_tokens"] = 44  # +10% output tokens: well under +25% total
    assert run(fake_api, "--no-post") == 0
    target.STATE["output_tokens"] = 400  # ~+1.5x total cost
    capsys.readouterr()
    assert run(fake_api) == 1
    out = capsys.readouterr().out
    assert "total cost is more than 1.25x the baseline's" in out
    assert EvalRunIn.model_validate(fake_api.runs[-1]).status == "regressed"


def test_api_down_still_runs_on_local_spans(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_api.down = True
    assert run(fake_api) == 0
    out, err = capsys.readouterr()
    assert "API not reachable" in err and "run not posted" in err
    assert "locally recorded spans (estimated cost)" in out
    assert f"${2 * ONE_CALL_COST:.5f}" in out  # estimated with the same prices as the server


def test_a_failed_post_never_changes_the_exit_code(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_api.post_run_status = 500
    assert run(fake_api) == 0
    assert "could not post the run" in capsys.readouterr().err


def test_mock_uses_local_spans_even_with_the_api_up(target: ModuleType, fake_api: FakeApi) -> None:
    assert run(fake_api, "--mock") == 0
    assert not any(p.startswith("GET /v1/traces/") for p in fake_api.paths)
    assert fake_api.runs  # --mock still posts unless --no-post


def test_target_error_fails_the_case_and_regresses(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake_api, "--update-baseline", "--no-post") == 0
    target.STATE["raise"] = True
    capsys.readouterr()
    assert run(fake_api, "--no-post") == 1
    assert "run (RuntimeError: target exploded)" in capsys.readouterr().out


def test_max_cost_stops_the_run_and_fails(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(fake_api, "--max-cost", "0.000001") == 1
    out = capsys.readouterr().out
    assert "skipped: --max-cost" in out and "--max-cost reached" in out
    run_in = EvalRunIn.model_validate(fake_api.runs[-1])
    assert run_in.status == "error"
    assert run_in.cases[1].trace_id is None and not run_in.cases[1].passed


def test_github_env_fills_the_git_fields(
    target: ModuleType, fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_HEAD_REF": "shorter-replies",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "seripavan7-alt/lucentpad",
        "GITHUB_RUN_ID": "42",
    }.items():
        monkeypatch.setenv(k, v)
    assert run(fake_api) == 0
    run_in = EvalRunIn.model_validate(fake_api.runs[-1])
    assert run_in.git_sha == "a" * 40 and run_in.git_ref == "shorter-replies"
    assert run_in.ci_url == "https://github.com/seripavan7-alt/lucentpad/actions/runs/42"


def test_usage_errors_exit_2(
    target: ModuleType, fake_api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    Path("nomock.yaml").write_text(SUITE.replace(":run", ":no_mock"), encoding="utf-8")
    assert main(["eval", "nomock.yaml", "--mock"], transport=fake_api.transport()) == 2
    assert "no mock parameter" in capsys.readouterr().err
    Path("bad.yaml").write_text("target: nope\ncases: []\n", encoding="utf-8")
    assert main(["eval", "bad.yaml"], transport=fake_api.transport()) == 2
    assert "target must be `module:function`" in capsys.readouterr().err
    Path("baseline.json").write_text("{not json", encoding="utf-8")
    assert run(fake_api) == 2
    assert main([], transport=fake_api.transport()) == 2


# --------------------------------------------------------------------------- suite + checks


def test_suite_rejects_bad_checks() -> None:
    for raw, message in [
        ({"nope": 1}, "unknown check"),
        ({"contains": "a", "regex": "b"}, "one-key mapping"),
        ({"regex": "("}, "invalid regex"),
        ({"max_cost_usd": -1}, "must not be negative"),
        ({"max_latency_ms": True}, "must be a number"),
        ({"tool_called": {"name": "x", "argz": {}}}, "unknown keys"),
    ]:
        with pytest.raises(SuiteError, match=message):
            parse_check("case 'x'", raw)
    assert parse_check("c", "no_tool").label == "no_tool"
    assert parse_check("c", {"no_tool": "issue_refund"}).label == "no_tool: issue_refund"
    assert parse_check("c", {"contains": 1043}).label == "contains: 1043"


def test_checks_semantics() -> None:
    obs = Observation(
        answer="I've passed it to a Colleague.",
        tools=(
            ToolCall("lookup_order", {"order_id": 1057}, "ok"),
            ToolCall("lookup_order", {"order_id": "9999"}, "error"),
        ),
        cost_usd=None,
        latency_ms=1200.0,
    )

    def check(raw: Any) -> tuple[bool, str | None]:
        r = evaluate(parse_check("c", raw), obs)
        return r.passed, r.detail

    assert check({"contains": "colleague"}) == (True, None)  # case-insensitive
    assert check({"not_contains": "COLLEAGUE"}) == (False, "found in the answer")
    assert check({"regex": "Colleague\\.$"})[0]
    assert check({"tool_called": {"name": "lookup_order", "args": {"order_id": "1057"}}})[0]
    assert check({"tool_called": {"name": "lookup_order", "args": {"order_id": "9999"}}})[0]
    assert check({"tool_called": {"name": "lookup_order", "args": {"order_id": "1"}}}) == (
        False,
        'lookup_order called with {"order_id": "9999"}',
    )
    assert check({"tool_called": "issue_refund"}) == (
        False,
        "not called; tools called: lookup_order",
    )
    assert check({"no_tool": "issue_refund"}) == (True, None)
    assert check("no_tool") == (False, "called: lookup_order")
    assert check({"max_cost_usd": 1}) == (False, "cost unknown")
    assert check({"max_latency_ms": 1000}) == (False, "latency 1200 ms > 1000 ms")


def test_blocked_calls_are_guardrail_spans_not_tool_calls() -> None:
    spans = [
        {"kind": "tool", "name": "lookup_order", "status": "ok", "start_time": "1",
         "attributes": {Attr.INPUT_PREVIEW: '{"order_id": "1057"}'}},
        {"kind": "guardrail", "name": "guardrail refund_limit", "status": "blocked",
         "start_time": "2", "attributes": {Attr.GEN_AI_TOOL_NAME: "issue_refund"}},
        {"kind": "tool", "name": "draft_email", "status": "ok", "start_time": "3",
         "attributes": {Attr.INPUT_PREVIEW: "not json"}},
    ]  # fmt: skip
    assert tool_calls(spans) == (
        ToolCall("lookup_order", {"order_id": "1057"}, "ok"),
        ToolCall("draft_email", None, "ok"),
    )


def test_constants_match_the_server() -> None:
    assert _eval.ATTR_EVAL_RUN_ID == Attr.EVAL_RUN_ID
    assert _eval.ATTR_EVAL_CASE == Attr.EVAL_CASE
    assert {m: tuple(p) for m, p in FALLBACK_PRICES.items()} == {
        m: tuple(p) for m, p in pricing.PRICES.items()
    }
    span = {
        "kind": "llm",
        "attributes": {
            Attr.GEN_AI_REQUEST_MODEL: "claude-haiku-4-5-20251001",
            Attr.GEN_AI_INPUT_TOKENS: 1200,
            Attr.GEN_AI_OUTPUT_TOKENS: 80,
            Attr.GEN_AI_CACHE_READ_TOKENS: 1000,
        },
    }
    assert spans_cost([span], FALLBACK_PRICES) == pricing.cost_usd(
        "claude-haiku-4-5-20251001", 1200, 80, 1000
    )
    assert spans_cost([{**span, "attributes": {Attr.GEN_AI_REQUEST_MODEL: "x"}}], {}) is None


# --------------------------------------------------------------------------- the demo suite


def test_demo_suite_passes_in_mock_mode_against_the_committed_baseline(
    fake_api: FakeApi, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LUCENTPAD_ENDPOINT", raising=False)
    fake_api.down = True  # fully offline: rules from the suite, local spans, fallback prices
    code = main(
        ["eval", str(REPO / "evals/support_agent.yaml"), "--mock", "--no-post"],
        transport=fake_api.transport(),
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "6 cases: 6 passed, 0 failed, 0 regressed" in out
    assert "(baseline $0.01041, +0.0%)" in out  # deterministic mock costs


def test_demo_suite_file_is_valid_and_complete() -> None:
    suite = load_suite(REPO / "evals/support_agent.yaml")
    assert [c.id for c in suite.cases] == [
        "order_status",
        "refund_under_limit",
        "refund_over_limit_blocked",
        "unknown_order",
        "reschedule_delivery",
        "small_talk_no_tool",
    ]
    assert suite.model == "claude-haiku-4-5"
    baseline = json.loads((REPO / "evals/baseline.json").read_text())
    assert set(baseline["cases"]) == {c.id for c in suite.cases}
    assert all(c["passed"] for c in baseline["cases"].values())


def broken_prompt() -> str:
    """config.SYSTEM_PROMPT with evals/demo-break-prompt.patch applied."""
    patch = (REPO / "evals/demo-break-prompt.patch").read_text().splitlines()
    removed = "\n".join(line[1:] for line in patch if line.startswith("-") and line[1:4] != "-- ")
    added = "\n".join(line[1:] for line in patch if line.startswith("+") and line[1:4] != "++ ")
    source = Path(config.__file__).read_text()
    assert removed in source, "the demo-break patch no longer applies to config.py"
    namespace: dict[str, Any] = {}
    exec(compile(source.replace(removed, added), "config.py", "exec"), namespace)  # noqa: S102
    prompt: str = namespace["SYSTEM_PROMPT"]
    assert prompt != config.SYSTEM_PROMPT
    return prompt


def test_demo_break_patch_fails_the_gate(
    fake_api: FakeApi, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SYSTEM_PROMPT", broken_prompt())
    fake_api.down = True
    code = main(
        ["eval", str(REPO / "evals/support_agent.yaml"), "--mock", "--no-post"],
        transport=fake_api.transport(),
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "order_status               REGRESSED" in out
    assert "tool_called: lookup_order" in out


def test_eval_workflow_is_valid_yaml() -> None:
    wf = yaml.safe_load((REPO / ".github/workflows/eval.yml").read_text())
    on = wf[True] if True in wf else wf["on"]  # YAML 1.1 reads `on` as True
    assert set(on["pull_request"]["paths"]) >= {"examples/**", "evals/**", "sdk/**"}
    (job,) = wf["jobs"].values()
    assert job["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    steps = job["steps"]
    skip = next(s for s in steps if "notice" in s.get("run", ""))
    assert skip["if"] == "env.ANTHROPIC_API_KEY == ''"
    evals = next(s for s in steps if s.get("name") == "lucentpad eval")
    assert "evals/support_agent.yaml" in evals["run"]
    assert evals["if"] == "env.ANTHROPIC_API_KEY != ''"
    text = (REPO / ".github/workflows/eval.yml").read_text()
    assert "sk-ant" not in text
    assert textwrap.dedent(text) == text
