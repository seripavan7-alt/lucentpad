from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from lucentpad_server.schema import MAX_BATCH_SPANS, Span, SpanBatch

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _span(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "span_id": "b7ad6b7169203331",
        "name": "chat claude-sonnet-5",
        "kind": "llm",
        "source": "sdk",
        "start_time": T0,
        "end_time": T0 + timedelta(seconds=1),
    }
    base.update(overrides)
    return base


def test_valid_span() -> None:
    span = Span.model_validate(_span(parent_span_id="00f067aa0ba902b7"))
    assert span.status == "ok"
    assert span.attributes == {}


@pytest.mark.parametrize(
    "trace_id",
    ["0AF7651916CD43DD8448EB211C80319C", "0af7651916cd43dd", "0" * 32, "g" * 32, ""],
)
def test_bad_trace_id(trace_id: str) -> None:
    with pytest.raises(ValidationError):
        Span.model_validate(_span(trace_id=trace_id))


@pytest.mark.parametrize("span_id", ["B7AD6B7169203331", "b7ad6b71", "0" * 16, "z" * 16])
def test_bad_span_id(span_id: str) -> None:
    with pytest.raises(ValidationError):
        Span.model_validate(_span(span_id=span_id))
    with pytest.raises(ValidationError):
        Span.model_validate(_span(parent_span_id=span_id))


def test_self_parent_rejected() -> None:
    with pytest.raises(ValidationError, match="own parent"):
        Span.model_validate(_span(parent_span_id="b7ad6b7169203331"))


def test_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError, match="end_time"):
        Span.model_validate(_span(end_time=T0 - timedelta(milliseconds=1)))


def test_zero_duration_allowed() -> None:
    assert Span.model_validate(_span(end_time=T0)).end_time == T0


def test_tz_naive_rejected() -> None:
    naive = datetime(2026, 9, 25, 12, 0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        Span.model_validate(_span(start_time=naive, end_time=naive))


def test_unknown_fields_and_values_rejected() -> None:
    with pytest.raises(ValidationError):
        Span.model_validate(_span(colour="red"))
    with pytest.raises(ValidationError):
        Span.model_validate(_span(kind="retriever"))
    with pytest.raises(ValidationError):
        Span.model_validate(_span(status="warning"))


def test_batch_limits() -> None:
    one = _span()
    with pytest.raises(ValidationError):
        SpanBatch.model_validate({"spans": []})
    assert len(SpanBatch.model_validate({"spans": [one] * MAX_BATCH_SPANS}).spans) == 1000
    with pytest.raises(ValidationError):
        SpanBatch.model_validate({"spans": [one] * (MAX_BATCH_SPANS + 1)})
