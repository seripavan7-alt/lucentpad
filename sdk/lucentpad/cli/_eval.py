"""``lucentpad eval``: run a suite, check each case, compare with the baseline, post the run.

Per case: the target runs inside its own ``lucentpad.trace`` (root span tagged with
``lucentpad.eval.run_id`` / ``lucentpad.eval.case``), so every model and tool call it makes is
traced. Tool calls and cost come from that trace, read back from ``GET /v1/traces/{id}`` (cost
is server-computed). With ``--mock``, or when the API can't be reached, they come from the spans
the SDK recorded in this process instead (cost estimated from token counts).

Exit codes: 0 no regression, 1 regression (a case that passed in the baseline fails, the total
cost is over 1.25x the baseline's, or ``--max-cost`` was reached), 2 usage error (bad suite or
baseline file, target not importable). Posting the run never changes the exit code.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TextIO

import httpx

import lucentpad

from .. import _core
from .._attrs import PREVIEW_MAX_CHARS
from .._exporter import Exporter, SpanDict
from ..guardrails import redact
from . import _baseline
from ._baseline import COST_RISE_LIMIT, Baseline, BaselineCase, BaselineError
from ._checks import CheckResult, Observation, evaluate_all, tool_calls
from ._prices import FALLBACK_PRICES, spans_cost
from ._suite import Case, Suite, SuiteError, load_suite

# Same values as lucentpad_server.schema.Attr.EVAL_RUN_ID / EVAL_CASE (a test checks them).
ATTR_EVAL_RUN_ID = "lucentpad.eval.run_id"
ATTR_EVAL_CASE = "lucentpad.eval.case"

DEFAULT_ENDPOINT = "http://localhost:8000"
PROBE_TIMEOUT = 1.0
READ_TIMEOUT = 5.0  # waiting for a case's spans to become readable from the API
POST_TIMEOUT = 5.0

EvalStatus = Literal["passed", "failed", "regressed", "error"]
Source = Literal["api", "local"]


# --------------------------------------------------------------------------- arguments


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("suite", help="suite file, e.g. evals/support_agent.yaml")
    p.add_argument(
        "--baseline", help="baseline file (default: baseline.json next to the suite file)"
    )
    p.add_argument(
        "--update-baseline", action="store_true", help="write this run's results as the baseline"
    )
    p.add_argument(
        "--max-cost",
        type=float,
        metavar="USD",
        help="stop and fail once the run has spent this much",
    )
    p.add_argument(
        "--endpoint",
        help="LucentPad API (default: $LUCENTPAD_ENDPOINT or http://localhost:8000)",
    )
    p.add_argument("--model", help="model for the target (overrides the suite's)")
    p.add_argument("--no-post", action="store_true", help="don't post the run to the API")
    p.add_argument(
        "--mock",
        action="store_true",
        help="run the target with its scripted model (no key, no cost) and check against the "
        "locally recorded spans",
    )


# --------------------------------------------------------------------------- local spans


class _RecordingExporter(Exporter):
    """Exports as usual, and keeps a copy of every finished span by trace id."""

    def __init__(self, endpoint: str, *, transport: httpx.BaseTransport | None) -> None:
        super().__init__(endpoint, transport=transport)
        self._recorded: dict[str, list[SpanDict]] = {}
        self._rec_lock = threading.Lock()

    def submit(self, span: SpanDict) -> None:
        with self._rec_lock:
            self._recorded.setdefault(str(span.get("trace_id")), []).append(dict(span))
        super().submit(span)

    def spans(self, trace_id: str) -> list[SpanDict]:
        with self._rec_lock:
            return list(self._recorded.get(trace_id, []))


# --------------------------------------------------------------------------- results


@dataclass
class CaseResult:
    case: Case
    checks: list[CheckResult]
    obs: Observation
    trace_id: str | None
    source: Source
    baseline: BaselineCase | None = None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def regressed(self) -> bool:
        return self.baseline is not None and self.baseline.passed and not self.passed


@dataclass
class RunReport:
    suite: Suite
    model: str | None
    mock: bool
    started_at: datetime
    duration_ms: float = 0.0
    cases: list[CaseResult] = field(default_factory=list)
    baseline: Baseline | None = None
    cost_note: str | None = None  # why the total cost wasn't compared
    cost_rise: bool = False
    max_cost_hit: bool = False

    @property
    def total_cost(self) -> float | None:
        costs = [c.obs.cost_usd for c in self.cases]
        if not costs or any(c is None for c in costs):
            return None
        return round(sum(c for c in costs if c is not None), 8)

    @property
    def regressions(self) -> list[CaseResult]:
        return [c for c in self.cases if c.regressed]

    @property
    def status(self) -> EvalStatus:
        if self.regressions or self.cost_rise:
            return "regressed"
        if self.max_cost_hit:
            return "error"
        if any(not c.passed for c in self.cases):
            return "failed"
        return "passed"

    @property
    def gate_failed(self) -> bool:
        return bool(self.regressions) or self.cost_rise or self.max_cost_hit


# --------------------------------------------------------------------------- the target


def load_target(spec: str) -> Callable[..., Any]:
    module_name, _, attr = spec.partition(":")
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    try:
        obj: Any = importlib.import_module(module_name)
        for part in attr.split("."):
            obj = getattr(obj, part)
    except (ImportError, AttributeError) as exc:
        raise SuiteError(f"target {spec}: {exc}") from None
    if not callable(obj):
        raise SuiteError(f"target {spec} is not callable")
    fn: Callable[..., Any] = obj
    return fn


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _answer_of(result: Any) -> tuple[str, str | None]:
    if isinstance(result, str):
        return result, None
    if isinstance(result, Mapping):
        tid = result.get("trace_id")
        return str(result.get("answer", "")), tid if isinstance(tid, str) else None
    answer = getattr(result, "answer", None)
    tid = getattr(result, "trace_id", None)
    return (str(answer) if answer is not None else str(result)), tid if isinstance(
        tid, str
    ) else None


# --------------------------------------------------------------------------- the API


class Api:
    def __init__(self, endpoint: str, transport: httpx.BaseTransport | None) -> None:
        self.base = endpoint.rstrip("/")
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(POST_TIMEOUT, connect=PROBE_TIMEOUT),
            headers={"user-agent": "lucentpad-eval"},
        )

    def reachable(self) -> bool:
        try:
            return self.client.get(self.base + "/healthz", timeout=PROBE_TIMEOUT).status_code == 200
        except httpx.HTTPError:
            return False

    def read_trace(self, trace_id: str, span_ids: set[str]) -> dict[str, Any] | None:
        """The trace detail once every span in ``span_ids`` is readable; None on timeout."""
        deadline = time.monotonic() + READ_TIMEOUT
        while True:
            try:
                resp = self.client.get(f"{self.base}/v1/traces/{trace_id}")
                if resp.status_code == 200:
                    body: dict[str, Any] = resp.json()
                    have = {s.get("span_id") for s in body.get("spans", [])}
                    if span_ids <= have:
                        return body
            except (httpx.HTTPError, ValueError):
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def post_run(self, payload: dict[str, Any]) -> str | None:
        """POST /v1/evals/runs; the new run's id, or raises ``httpx.HTTPError``/``ValueError``."""
        resp = self.client.post(self.base + "/v1/evals/runs", json=payload)
        resp.raise_for_status()
        run_id = resp.json().get("id")
        return run_id if isinstance(run_id, str) else None

    def close(self) -> None:
        self.client.close()


# --------------------------------------------------------------------------- running


def _observe_local(
    spans: list[SpanDict], answer: str, latency: float, error: str | None
) -> Observation:
    table = _core.current_prices() or FALLBACK_PRICES
    return Observation(answer, tool_calls(spans), spans_cost(spans, table), latency, error)


def _observe_api(detail: dict[str, Any], answer: str, latency: float) -> Observation:
    cost = (detail.get("trace") or {}).get("cost_usd")
    return Observation(
        answer,
        tool_calls(detail.get("spans", [])),
        float(cost) if isinstance(cost, int | float) else None,
        latency,
    )


def _run_case(
    case: Case,
    *,
    suite: Suite,
    target: Callable[..., Any],
    kwargs: dict[str, Any],
    run_id: str,
    recorder: _RecordingExporter,
    api: Api | None,
) -> CaseResult:
    answer, error = "", None
    tags: dict[str, Any] = {ATTR_EVAL_RUN_ID: run_id, ATTR_EVAL_CASE: case.id}
    tracer = lucentpad.trace(f"eval {suite.name}/{case.id}", input=case.input, **tags)
    target_tid: str | None = None
    start = time.perf_counter()
    try:
        with tracer:
            answer, target_tid = _answer_of(target(case.input, **kwargs))
            tracer.set_output(answer)
    except Exception as exc:  # the target failed: the case fails, the run goes on
        error = f"{type(exc).__name__}: {exc}"
    latency = round((time.perf_counter() - start) * 1000, 1)
    local = recorder.spans(tracer.trace_id)
    trace_id = tracer.trace_id if local else target_tid

    obs: Observation | None = None
    source: Source = "local"
    if api is not None and local and error is None and lucentpad.flush(READ_TIMEOUT):
        detail = api.read_trace(tracer.trace_id, {str(s["span_id"]) for s in local})
        if detail is not None:
            obs, source = _observe_api(detail, answer, latency), "api"
    if obs is None:
        obs = _observe_local(local, answer, latency, error)
    return CaseResult(case, evaluate_all(case.checks, obs), obs, trace_id, source)


def run_suite(
    suite: Suite,
    *,
    target: Callable[..., Any],
    kwargs: dict[str, Any],
    model: str | None,
    mock: bool,
    max_cost: float | None,
    recorder: _RecordingExporter,
    api: Api | None,
) -> RunReport:
    report = RunReport(suite, model, mock, datetime.now(UTC))
    run_id = uuid.uuid4().hex
    t0 = time.perf_counter()
    spent = 0.0
    for case in suite.cases:
        if max_cost is not None and spent > max_cost:
            report.max_cost_hit = True
            skipped = CheckResult("max_cost", False, f"skipped: --max-cost ${max_cost:g} reached")
            report.cases.append(
                CaseResult(case, [skipped], Observation("", (), None, 0.0), None, "local")
            )
            continue
        result = _run_case(
            case, suite=suite, target=target, kwargs=kwargs, run_id=run_id, recorder=recorder,
            api=api,
        )  # fmt: skip
        spent += result.obs.cost_usd or 0.0
        report.cases.append(result)
    if max_cost is not None and spent > max_cost:
        report.max_cost_hit = True
    report.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    return report


def compare(report: RunReport, baseline: Baseline | None) -> None:
    report.baseline = baseline
    if baseline is None:
        return
    for c in report.cases:
        c.baseline = baseline.cases.get(c.case.id)
    why_not = _baseline.cost_comparable(baseline, model=report.model, mock=report.mock)
    total = report.total_cost
    if why_not is None and total is None:
        why_not = "this run's cost is unknown"
    if why_not is not None:
        report.cost_note = f"cost not compared: {why_not}"
        return
    assert baseline.total_cost_usd is not None and total is not None
    report.cost_rise = total > baseline.total_cost_usd * COST_RISE_LIMIT


def to_baseline(report: RunReport) -> Baseline:
    return Baseline(
        suite=report.suite.name,
        model=report.model,
        mock=report.mock,
        total_cost_usd=report.total_cost,
        cases={c.case.id: BaselineCase(c.passed, c.obs.cost_usd) for c in report.cases},
    )


# --------------------------------------------------------------------------- output


def _money(v: float | None) -> str:
    return "-" if v is None else f"${v:.5f}"


def format_table(report: RunReport) -> str:
    header = ("case", "result", "baseline", "cost", "latency", "failed checks")
    rows: list[tuple[str, ...]] = []
    for c in report.cases:
        result = "REGRESSED" if c.regressed else "pass" if c.passed else "FAIL"
        if report.baseline is None:
            base = "-"
        elif c.baseline is None:
            base = "new"
        else:
            base = "pass" if c.baseline.passed else "fail"
        failed = "; ".join(
            f"{r.check} ({r.detail})" if r.detail else r.check for r in c.checks if not r.passed
        )
        latency = f"{c.obs.latency_ms:.0f} ms" if c.obs.latency_ms else "-"
        rows.append((c.case.id, result, base, _money(c.obs.cost_usd), latency, failed))
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header) - 1)]

    def line(r: tuple[str, ...]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(r[:-1], widths, strict=True)) + "  " + r[-1]

    out = [line(header), line((*("-" * w for w in widths), "-" * 13))]
    out.extend(line(r) for r in rows)
    return "\n".join(s.rstrip() for s in out)


def format_summary(report: RunReport) -> str:
    n = len(report.cases)
    passed = sum(c.passed for c in report.cases)
    parts = [
        f"{n} cases: {passed} passed, {n - passed} failed, {len(report.regressions)} regressed"
    ]
    total = report.total_cost
    cost = f"cost {_money(total)}"
    base = report.baseline.total_cost_usd if report.baseline is not None else None
    if total is not None and base:
        cost += f" (baseline {_money(base)}, {(total / base - 1) * 100:+.1f}%)"
    parts.append(cost)
    parts.append(f"status {report.status}")
    lines = [" · ".join(parts)]
    if report.cost_rise:
        lines.append(f"REGRESSION: total cost is more than {COST_RISE_LIMIT:g}x the baseline's")
    if report.max_cost_hit:
        lines.append("FAILED: --max-cost reached")
    if report.cost_note:
        lines.append(report.cost_note)
    return "\n".join(lines)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def git_info(env: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
    """(sha, ref, CI URL): from GitHub Actions' GITHUB_* variables, else the local checkout."""
    if env.get("GITHUB_ACTIONS") == "true":
        sha = env.get("GITHUB_SHA") or None
        ref = env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME") or None
        server, repo, run = (
            env.get("GITHUB_SERVER_URL"),
            env.get("GITHUB_REPOSITORY"),
            env.get("GITHUB_RUN_ID"),
        )
        url = f"{server}/{repo}/actions/runs/{run}" if server and repo and run else None
        return sha, ref, url
    return _git("rev-parse", "HEAD"), _git("rev-parse", "--abbrev-ref", "HEAD"), None


def _cut(value: str | None, n: int) -> str | None:
    return value[:n] if value is not None else None


def payload(report: RunReport, env: Mapping[str, str]) -> dict[str, Any]:
    """The ``POST /v1/evals/runs`` body (``schema.EvalRunIn``)."""
    sha, ref, url = git_info(env)
    base = report.baseline
    cases = []
    for c in report.cases[:500]:
        preview = redact(c.obs.answer).text[:PREVIEW_MAX_CHARS] if c.obs.answer else None
        cases.append(
            {
                "case": c.case.id,
                "passed": c.passed,
                "baseline_passed": c.baseline.passed if c.baseline is not None else None,
                "checks": [
                    {
                        "check": r.check,
                        "passed": r.passed,
                        "detail": redact(r.detail).text if r.detail else None,
                    }
                    for r in c.checks
                ],
                "cost_usd": c.obs.cost_usd,
                "latency_ms": c.obs.latency_ms if c.obs.latency_ms else None,
                "trace_id": c.trace_id,
                "output_preview": preview,
            }
        )
    return {
        "suite": report.suite.name,
        "status": report.status,
        "started_at": report.started_at.isoformat(),
        "duration_ms": report.duration_ms,
        "model": report.model,
        "git_sha": _cut(sha, 64),
        "git_ref": _cut(ref, 200),
        "ci_url": _cut(url, 500),
        "cost_usd": report.total_cost,
        "baseline_cost_usd": base.total_cost_usd if base is not None else None,
        "cases": cases,
    }


# --------------------------------------------------------------------------- command


def run_eval(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    env = os.environ if env is None else env

    def note(msg: str) -> None:
        print(msg, file=err)

    try:
        suite = load_suite(args.suite)
        target = load_target(suite.target)
        baseline_path = Path(args.baseline) if args.baseline else suite.path.with_name(
            "baseline.json"
        )  # fmt: skip
        baseline = _baseline.load(baseline_path)
    except (SuiteError, BaselineError) as exc:
        note(f"lucentpad eval: {exc}")
        return 2
    if baseline is not None and baseline.suite and baseline.suite != suite.name:
        note(f"warning: {baseline_path} is the baseline of suite {baseline.suite!r}")

    model = args.model or suite.model
    kwargs: dict[str, Any] = {}
    if model is not None:
        if _accepts(target, "model"):
            kwargs["model"] = model
        else:
            note(f"warning: target {suite.target} takes no model argument; ignoring {model}")
    if args.mock:
        if not _accepts(target, "mock"):
            note(f"lucentpad eval: target {suite.target} has no mock parameter; can't --mock")
            return 2
        kwargs["mock"] = True

    endpoint = args.endpoint or env.get("LUCENTPAD_ENDPOINT", "").strip() or DEFAULT_ENDPOINT
    api = Api(endpoint, transport)
    api_up = api.reachable()
    use_local_rules = suite.rules is not None and (args.mock or not api_up)
    if not api_up:
        note(f"LucentPad API not reachable at {endpoint}: checking locally recorded spans")
    note(
        "guardrail rules: "
        + ("the suite's (local)" if use_local_rules else "the API's" if api_up else "none")
    )

    recorder = _RecordingExporter(endpoint, transport=transport)
    _core.configure(
        endpoint,
        service_name=suite.service,
        capture_content=True,
        enabled=True,
        rules=suite.rules if use_local_rules else None,
        exporter=recorder,
    )
    if _core.active() is None:
        note("warning: tracing is disabled (LUCENTPAD_DISABLED); tool and cost checks will fail")
    try:
        report = run_suite(
            suite,
            target=target,
            kwargs=kwargs,
            model=model,
            mock=args.mock,
            max_cost=args.max_cost,
            recorder=recorder,
            api=api if api_up and not args.mock else None,
        )
        compare(report, baseline)
        sources = {c.source for c in report.cases if c.trace_id}
        print(f"lucentpad eval: {suite.name} ({len(suite.cases)} cases, model {model or '-'}"
              f"{', mock' if args.mock else ''})", file=out)  # fmt: skip
        print(format_table(report), file=out)
        print(format_summary(report), file=out)
        if sources:
            how = (
                "the LucentPad API (server-computed cost)"
                if sources == {"api"}
                else "locally recorded spans (estimated cost)"
                if sources == {"local"}
                else "the API and, where it lagged, local spans"
            )
            print(f"tool calls and cost from {how}", file=out)
        if baseline is None:
            print(f"no baseline at {baseline_path}: nothing to compare", file=out)

        if args.update_baseline:
            _baseline.save(baseline_path, to_baseline(report))
            print(f"baseline written to {baseline_path}", file=out)

        if args.no_post:
            pass
        elif not api_up:
            note("run not posted: the API is not reachable")
        else:
            try:
                run_ref = api.post_run(payload(report, env))
                note(f"run posted{f' ({run_ref})' if run_ref else ''}")
            except (httpx.HTTPError, ValueError) as exc:
                note(f"warning: could not post the run: {exc}")
    finally:
        lucentpad.flush(2.0 if api_up else 0.5)
        lucentpad.shutdown()
        api.close()

    if args.update_baseline:
        return 0
    return 1 if report.gate_failed else 0
