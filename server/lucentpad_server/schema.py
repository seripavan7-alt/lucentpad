"""Span schema and API models: the contract shared by the SDK, gateway, API and dashboard.

Spans are OpenTelemetry-shaped: W3C trace/span IDs, parent links, and a flat attribute
map using the OTel GenAI semantic convention names where they fit (see ``Attr``). This
lets an OTel exporter be added later without a schema change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
MAX_BATCH_SPANS = 1000
PREVIEW_MAX_CHARS = 2000
"""Longest prompt/response preview kept (``Attr.INPUT_PREVIEW`` / ``Attr.OUTPUT_PREVIEW``)."""
FACET_MAX_VALUES = 50


class Attr:
    """Attribute keys. ``gen_ai.*`` follow the OTel GenAI conventions; ``lucentpad.*`` are ours."""

    SERVICE_NAME = "service.name"
    GEN_AI_SYSTEM = "gen_ai.system"  # "anthropic" | "openai"
    GEN_AI_OPERATION = "gen_ai.operation.name"  # "chat" | "execute_tool"
    GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
    GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
    GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    # Cached input, as subsets of GEN_AI_INPUT_TOKENS (which counts all input, cached included).
    GEN_AI_CACHE_READ_TOKENS = "gen_ai.usage.cache_read.input_tokens"
    GEN_AI_CACHE_CREATION_TOKENS = "gen_ai.usage.cache_creation.input_tokens"
    GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"
    GEN_AI_TOOL_NAME = "gen_ai.tool.name"
    COST_USD = "lucentpad.cost_usd"
    STREAMING = "lucentpad.streaming"
    CLIENT = "lucentpad.client"  # "claude-code" | "copilot-chat" | "copilot-cli" | "sdk"
    SESSION_ID = "lucentpad.session_id"
    GUARDRAIL_RULE = "lucentpad.guardrail.rule"
    # Prompt/response previews (<= PREVIEW_MAX_CHARS); *_TRUNCATED is true when cut at capture.
    INPUT_PREVIEW = "lucentpad.input.preview"
    OUTPUT_PREVIEW = "lucentpad.output.preview"
    INPUT_TRUNCATED = "lucentpad.input.truncated"
    OUTPUT_TRUNCATED = "lucentpad.output.truncated"
    # Failover event attributes
    FAILOVER_FROM_MODEL = "lucentpad.failover.from_model"
    FAILOVER_TO_MODEL = "lucentpad.failover.to_model"
    FAILOVER_STATUS_CODE = "lucentpad.failover.status_code"
    FAILOVER_RETRIES = "lucentpad.failover.retries"
    # Budget alert event attributes
    BUDGET_LIMIT_USD = "lucentpad.budget.limit_usd"
    BUDGET_SPENT_USD = "lucentpad.budget.spent_usd"
    # Redaction event attributes
    REDACTION_KIND = "lucentpad.redaction.kind"
    REDACTION_COUNT = "lucentpad.redaction.count"
    # Guardrails, budgets, evals (M3)
    GUARDRAIL_REASON = "lucentpad.guardrail.reason"  # human-readable why, on the guardrail span
    BUDGET_SCOPE = "lucentpad.budget.scope"  # "run" (SDK trace) | "session" (gateway)
    EVAL_RUN_ID = "lucentpad.eval.run_id"  # on the root span of a trace produced by an eval case
    EVAL_CASE = "lucentpad.eval.case"
    # Demo support agent
    REFUND_AMOUNT = "lucentpad.refund.amount"
    # Gateway (M2)
    GATEWAY_UPSTREAM = "lucentpad.gateway.upstream"  # "anthropic" | "openai"
    TTFB_MS = "lucentpad.ttfb_ms"  # time to the first response byte from the upstream
    # Salted SHA-256 of the client's API key, first 12 hex chars. Groups sessions; never the key.
    KEY_FINGERPRINT = "lucentpad.key_fingerprint"


class EventName:
    FAILOVER = "lucentpad.failover"
    BUDGET_ALERT = "lucentpad.budget.alert"
    REDACTION = "lucentpad.redaction"
    GUARDRAIL_BLOCK = "lucentpad.guardrail.block"


def _hex_id(length: int) -> AfterValidator:
    def check(value: str) -> str:
        if len(value) != length or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"must be {length} lowercase hex characters")
        if value == "0" * length:
            raise ValueError("must not be all zeros")
        return value

    return AfterValidator(check)


TraceId = Annotated[str, _hex_id(32)]
SpanId = Annotated[str, _hex_id(16)]
AttrValue = str | int | float | bool | list[str]
Attributes = dict[str, AttrValue]

SpanKind = Literal["agent", "llm", "tool", "guardrail"]
"""agent: root of an SDK run or a gateway session. llm: one model call.
tool: a ``@span`` step. guardrail: a blocked request, recorded as its own span."""

SpanStatus = Literal["ok", "error", "blocked"]
SpanSource = Literal["sdk", "gateway"]
TraceOrder = Literal["desc", "asc"]
"""Direction of the traces list sort: descending (default) or ascending."""
GatewayProvider = Literal["anthropic", "openai"]
"""Upstreams the gateway forwards to: Anthropic Messages API, OpenAI Chat Completions."""

TraceSort = Literal["started", "duration", "name", "source", "cost"]
"""What the traces list sorts by: start time (default), duration, trace name, source or cost.
Ties break on ``trace_id`` in the same direction, so paging is stable."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SpanEvent(_Model):
    """A point-in-time occurrence on a span, such as a failover or a budget alert."""

    name: str = Field(min_length=1, max_length=200)
    time: datetime
    attributes: Attributes = Field(default_factory=dict)


class Span(_Model):
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None
    name: str = Field(min_length=1, max_length=200)
    kind: SpanKind
    source: SpanSource
    start_time: datetime
    end_time: datetime
    status: SpanStatus = "ok"
    status_message: str | None = Field(default=None, max_length=2000)
    attributes: Attributes = Field(default_factory=dict)
    events: list[SpanEvent] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _check(self) -> Span:
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be before start_time")
        if self.parent_span_id == self.span_id:
            raise ValueError("a span cannot be its own parent")
        return self


class SpanBatch(_Model):
    """Body of ``POST /v1/spans``."""

    spans: list[Span] = Field(min_length=1, max_length=MAX_BATCH_SPANS)


class IngestAccepted(_Model):
    accepted: int = Field(description="Spans queued for writing (always the whole batch).")


class IngestStats(_Model):
    """Counters of the ingest pipeline since the API started."""

    queue_depth: int = Field(description="Spans accepted but not yet written.")
    queue_capacity: int
    accepted_total: int
    rejected_total: int = Field(description="Spans refused with 429 because the queue was full.")
    written_total: int
    write_errors_total: int = Field(description="Spans dropped after the writer gave up retrying.")


class ErrorResponse(_Model):
    detail: str


class TraceSummary(_Model):
    """One row of the traces list. Aggregates are computed over the trace's spans."""

    trace_id: TraceId
    name: str
    source: SpanSource
    service_name: str | None
    client: str | None
    start_time: datetime
    duration_ms: float
    status: SpanStatus
    span_count: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    models: list[str]
    sample: bool = Field(
        default=False, description="Startup sample data (not something you recorded)."
    )
    input_preview: str | None = Field(
        description="The run's first user message (root span's input, else the earliest llm "
        "span's); null when capture is off."
    )
    output_preview: str | None = Field(
        description="The run's final answer (root span's output, else the latest llm span's)."
    )


class TraceList(_Model):
    traces: list[TraceSummary]
    next_cursor: str | None = Field(
        description="Opaque cursor for the next page; null when there are no more traces. "
        "Valid only with the same order and filters."
    )
    as_of: datetime = Field(
        description="Server time of this response; pass it as `since` on the next live poll."
    )


class TraceDetail(_Model):
    trace: TraceSummary
    spans: list[Span] = Field(
        description="Spans ordered by start_time: all of them, or with `since` only those "
        "stored after it."
    )
    as_of: datetime = Field(
        description="Server time of this response; pass it as `since` on the next live poll."
    )


class DataInfo(_Model):
    """What the database holds: the startup sample, real recorded data, or both."""

    sample_data: bool
    real_data: bool


class FacetValue(_Model):
    value: str
    count: int


class TraceFacets(_Model):
    """Value counts per filter. Each facet's counts apply every other filter but not its own,
    so ticking a value never hides its siblings. At most ``FACET_MAX_VALUES`` values per facet,
    by count descending, then value."""

    name: list[FacetValue]
    status: list[FacetValue]
    source: list[FacetValue]
    client: list[FacetValue]
    model: list[FacetValue]
    service: list[FacetValue]


class GatewayTurn(_Model):
    """One model call through the gateway (a ``kind=llm`` span with ``source=gateway``)."""

    trace_id: TraceId = Field(description="The session trace this turn belongs to.")
    span_id: SpanId
    client: str | None = Field(
        description="`claude-code`, `copilot-cli`, `copilot-chat` or `other`."
    )
    provider: GatewayProvider | None
    model: str | None
    start_time: datetime
    duration_ms: float
    ttfb_ms: float | None
    status: SpanStatus
    streaming: bool
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    failover: bool = Field(description="True when the request was retried on a fallback model.")
    input_preview: str | None
    output_preview: str | None


class GatewayTurnList(_Model):
    turns: list[GatewayTurn] = Field(description="Newest first.")
    next_cursor: str | None
    as_of: datetime = Field(description="Server time; pass it as `since` on the next live poll.")


class GatewayClientTotals(_Model):
    client: str
    sessions: int
    turns: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class GatewaySummary(_Model):
    """Per-client totals over a time window, most expensive first."""

    clients: list[GatewayClientTotals]
    as_of: datetime


# --------------------------------------------------------------------------- M3: guardrails

GuardrailRuleType = Literal["prompt", "tool"]
GuardrailEventKind = Literal["block", "redaction", "budget"]


class GuardrailRule(_Model):
    """A blocking rule. ``prompt`` rules match the user's message (any keyword, case-insensitive,
    or the regex); ``tool`` rules match a tool call by name and a condition on its arguments,
    e.g. ``amount > 200`` (comparisons on argument fields; no code is evaluated)."""

    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    type: GuardrailRuleType
    description: str | None = None
    keywords: list[str] = Field(default_factory=list, description="prompt rules")
    pattern: str | None = Field(default=None, description="prompt rules: a regex")
    tool: str | None = Field(default=None, description="tool rules: the tool name")
    condition: str | None = Field(default=None, description="tool rules, e.g. `amount > 200`")
    message: str = Field(description="Shown to the caller when the rule blocks.")


class GuardrailRules(_Model):
    rules: list[GuardrailRule]
    source: str = Field(description="Where the rules came from: a file path or `built-in`.")
    version: str = Field(description="Changes whenever the rules change (for client caches).")


class GuardrailEvent(_Model):
    """A block (a ``kind=guardrail`` span), a redaction (a ``lucentpad.redaction`` event) or a
    budget alert (a ``lucentpad.budget.alert`` event)."""

    kind: GuardrailEventKind
    time: datetime
    trace_id: TraceId
    span_id: SpanId
    source: SpanSource
    client: str | None
    rule: str | None = Field(description="blocks: the rule id")
    reason: str | None = Field(description="blocks: why")
    redaction_kind: str | None = Field(description="redactions: email, api_key, card, ...")
    count: int = Field(description="redactions: how many values; blocks and budget alerts: 1")
    budget_limit_usd: float | None = Field(default=None, description="budget alerts")
    budget_spent_usd: float | None = Field(default=None, description="budget alerts")
    budget_scope: str | None = Field(default=None, description="budget alerts: run | session")


class GuardrailEventList(_Model):
    events: list[GuardrailEvent] = Field(description="Newest first.")
    next_cursor: str | None
    as_of: datetime


class GuardrailRuleCount(_Model):
    rule: str
    blocks: int


class GuardrailSummary(_Model):
    """Counts for a window: blocks per rule, redactions per kind."""

    blocks: list[GuardrailRuleCount]
    redactions: list[FacetValue]
    budget_alerts: int = 0
    as_of: datetime


# --------------------------------------------------------------------------- M3: pricing, costs


class ModelPrice(_Model):
    """USD per million tokens."""

    model: str
    input: float
    output: float
    cache_read: float
    cache_write: float


class PriceTable(_Model):
    prices: list[ModelPrice]
    checked: str = Field(description="When the list prices were last checked (ISO date).")


CostGroup = Literal["model", "client", "service"]


class CostPoint(_Model):
    bucket: datetime = Field(description="Start of the time bucket.")
    group: str = Field(description="Model, client or service name (`other` when unknown).")
    cost_usd: float
    input_tokens: int
    output_tokens: int
    calls: int


class CostSeries(_Model):
    """Spend over time, one point per (bucket, group) with any cost. Buckets are aligned to
    ``bucket_seconds`` (chosen from the window: 5 min up to 1 day)."""

    points: list[CostPoint]
    bucket_seconds: int
    group_by: CostGroup
    total_cost_usd: float
    as_of: datetime


# --------------------------------------------------------------------------- M3: evals

EvalStatus = Literal["passed", "failed", "regressed", "error"]


class EvalCheckResult(_Model):
    check: str = Field(description="e.g. `contains: refund`, `tool_called: lookup_order`.")
    passed: bool
    detail: str | None = None


class EvalCaseResult(_Model):
    case: str
    passed: bool
    baseline_passed: bool | None = Field(description="null when the case is new.")
    checks: list[EvalCheckResult]
    cost_usd: float | None
    latency_ms: float | None
    trace_id: TraceId | None
    output_preview: str | None


class EvalRunIn(_Model):
    """Body of ``POST /v1/evals/runs`` (sent by ``lucentpad eval``)."""

    suite: str = Field(min_length=1, max_length=200)
    status: EvalStatus
    started_at: datetime
    duration_ms: float
    model: str | None
    git_sha: str | None = Field(default=None, max_length=64)
    git_ref: str | None = Field(default=None, max_length=200)
    ci_url: str | None = Field(default=None, max_length=500)
    cost_usd: float | None
    baseline_cost_usd: float | None
    cases: list[EvalCaseResult] = Field(max_length=500)


class EvalRun(EvalRunIn):
    id: str


class EvalRunSummary(_Model):
    id: str
    suite: str
    status: EvalStatus
    started_at: datetime
    passed: int
    failed: int
    regressions: int
    cost_usd: float | None
    baseline_cost_usd: float | None = None
    git_sha: str | None
    git_ref: str | None
    ci_url: str | None


class EvalRunList(_Model):
    runs: list[EvalRunSummary] = Field(description="Newest first.")
    next_cursor: str | None
