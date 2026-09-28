"""Redaction of spans before they are stored (D20: the ingest API is the last line).

``redact_span`` runs the shared engine (``lucentpad.guardrails.redact``) over every string the
span carries (attribute values, string lists, ``status_message`` and event attribute values)
and, for each kind of value it replaced that the span has no ``lucentpad.redaction`` event for
yet, adds one (kind, count). Values are never logged or kept. The SDK and the gateway redact
before this, so for their spans this is normally a no-op.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable

from lucentpad.guardrails import redact
from lucentpad_server.schema import Attr, Attributes, AttrValue, EventName, Span, SpanEvent

log = logging.getLogger(__name__)

MAX_EVENTS = 100  # schema.Span.events max_length
MAX_MESSAGE_CHARS = 2000  # schema.Span.status_message max_length (a placeholder can be longer)

# Keys that are not free text, so never hold a secret: skipped for speed.
_SKIP_KEYS = frozenset(
    {
        Attr.GEN_AI_SYSTEM,
        Attr.GEN_AI_OPERATION,
        Attr.GEN_AI_REQUEST_MODEL,
        Attr.GEN_AI_RESPONSE_MODEL,
        Attr.GEN_AI_FINISH_REASONS,
        Attr.CLIENT,
        Attr.GATEWAY_UPSTREAM,
        Attr.KEY_FINGERPRINT,
        Attr.REDACTION_KIND,
    }
)


class _Redactor:
    __slots__ = ("counts",)

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def text(self, value: str) -> str:
        result = redact(value)
        if not result.changed:
            return value
        for kind, n in result.counts.items():
            self.counts[kind] += n
        return result.text

    def value(self, value: AttrValue) -> AttrValue:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            out = [self.text(v) for v in value]
            return out if out != value else value
        return value

    def attributes(self, attrs: Attributes) -> Attributes | None:
        """A redacted copy of ``attrs``, or None when nothing changed."""
        changed: Attributes | None = None
        for key, value in attrs.items():
            if key in _SKIP_KEYS:
                continue
            new = self.value(value)
            if new is not value:
                if changed is None:
                    changed = dict(attrs)
                changed[key] = new
        return changed


def _existing_kinds(events: Iterable[SpanEvent]) -> set[str]:
    return {
        str(e.attributes.get(Attr.REDACTION_KIND))
        for e in events
        if e.name == EventName.REDACTION and Attr.REDACTION_KIND in e.attributes
    }


def redact_span(span: Span) -> Span:
    """``span`` with every detected value replaced (the same object when nothing matched)."""
    r = _Redactor()
    attrs = r.attributes(span.attributes)
    message = span.status_message
    if message:
        message = r.text(message)[:MAX_MESSAGE_CHARS]
    events = list(span.events)
    events_changed = False
    for i, event in enumerate(events):
        if event.name == EventName.REDACTION:
            continue
        new_attrs = r.attributes(event.attributes)
        if new_attrs is not None:
            events[i] = event.model_copy(update={"attributes": new_attrs})
            events_changed = True
    if not r.counts:
        return span
    have = _existing_kinds(span.events)
    for kind in sorted(r.counts):
        if kind in have or len(events) >= MAX_EVENTS:
            continue
        events.append(
            SpanEvent(
                name=EventName.REDACTION,
                time=span.start_time,
                attributes={Attr.REDACTION_KIND: kind, Attr.REDACTION_COUNT: r.counts[kind]},
            )
        )
        events_changed = True
    update: dict[str, object] = {"status_message": message}
    if attrs is not None:
        update["attributes"] = attrs
    if events_changed:
        update["events"] = events
    return span.model_copy(update=update)


def _strip_previews(span: Span) -> Span:
    attrs = {
        k: v
        for k, v in span.attributes.items()
        if k not in (Attr.INPUT_PREVIEW, Attr.OUTPUT_PREVIEW)
    }
    return span.model_copy(update={"attributes": attrs})


def redact_spans(spans: list[Span]) -> list[Span]:
    """``redact_span`` over a batch. If redaction itself fails on a span, its previews are
    dropped rather than stored unredacted (logged once per batch, without the content)."""
    out: list[Span] = []
    failed = 0
    for span in spans:
        try:
            out.append(redact_span(span))
        except Exception:
            failed += 1
            out.append(_strip_previews(span))
    if failed:
        log.error("redaction failed on %d span(s); their previews were not stored", failed)
    return out
