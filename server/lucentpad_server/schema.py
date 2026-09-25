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


class Attr:
    """Attribute keys. ``gen_ai.*`` follow the OTel GenAI conventions; ``lucentpad.*`` are ours."""

    SERVICE_NAME = "service.name"
    GEN_AI_SYSTEM = "gen_ai.system"  # "anthropic" | "openai"
    GEN_AI_OPERATION = "gen_ai.operation.name"  # "chat" | "execute_tool"
    GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
    GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
    GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"
    GEN_AI_TOOL_NAME = "gen_ai.tool.name"
    COST_USD = "lucentpad.cost_usd"
    STREAMING = "lucentpad.streaming"
    CLIENT = "lucentpad.client"  # "claude-code" | "copilot-chat" | "copilot-cli" | "sdk"
    SESSION_ID = "lucentpad.session_id"
    GUARDRAIL_RULE = "lucentpad.guardrail.rule"


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
    accepted: int


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


class TraceList(_Model):
    traces: list[TraceSummary]
    next_cursor: str | None = Field(
        description="Opaque cursor for the next page; null when there are no more traces."
    )


class TraceDetail(_Model):
    trace: TraceSummary
    spans: list[Span] = Field(description="All spans in the trace, ordered by start_time.")
