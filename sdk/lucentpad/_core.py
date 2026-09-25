"""SDK state, span records, parenting (contextvars), ``trace()`` and ``@span``."""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import logging
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal, Self

from ._attrs import NAME_MAX_CHARS, STATUS_MESSAGE_MAX_CHARS, Attr
from ._exporter import Exporter, SpanDict
from ._previews import truncate

log = logging.getLogger("lucentpad")

DEFAULT_ENDPOINT = "http://localhost:8000"
SpanKindAll = Literal["agent", "llm", "tool", "guardrail"]
AttrValue = str | int | float | bool | list[str]


# --------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class Config:
    endpoint: str
    service_name: str | None
    capture_content: bool


_lock = threading.Lock()
_config: Config | None = None
_exporter: Exporter | None = None
_atexit_registered = False


def _env_disabled() -> bool:
    return os.environ.get("LUCENTPAD_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def configure(
    endpoint: str,
    *,
    service_name: str | None,
    capture_content: bool,
    enabled: bool,
    exporter: Exporter | None = None,
) -> None:
    """Implementation of ``lucentpad.init``. ``exporter`` is a test seam."""
    global _config, _exporter, _atexit_registered
    old: Exporter | None
    with _lock:
        old, _exporter, _config = _exporter, None, None
    if old is not None:
        old.shutdown(timeout=1.0)
    if not enabled or _env_disabled():
        return
    if endpoint == DEFAULT_ENDPOINT:
        endpoint = os.environ.get("LUCENTPAD_ENDPOINT", "").strip() or endpoint
    new = exporter if exporter is not None else Exporter(endpoint)
    with _lock:
        _config = Config(endpoint, service_name, capture_content)
        _exporter = new
        if not _atexit_registered:
            atexit.register(_atexit_shutdown)
            _atexit_registered = True


def active() -> Config | None:
    return _config


def current_exporter() -> Exporter | None:
    return _exporter


def flush(timeout: float) -> bool:
    exp = _exporter
    if exp is None:
        return True
    try:
        return exp.flush(timeout)
    except Exception:
        return False


def shutdown(timeout: float = 2.0) -> None:
    global _config, _exporter
    with _lock:
        exp, _exporter, _config = _exporter, None, None
    if exp is not None:
        try:
            exp.shutdown(timeout)
        except Exception:
            log.debug("lucentpad: shutdown error", exc_info=True)


def _atexit_shutdown() -> None:
    shutdown(timeout=2.0)


# --------------------------------------------------------------------------- span records


def _now() -> datetime:
    return datetime.now(UTC)


def new_trace_id() -> str:
    while (tid := secrets.token_hex(16)) == "0" * 32:
        pass
    return tid


def new_span_id() -> str:
    while (sid := secrets.token_hex(8)) == "0" * 16:
        pass
    return sid


def clean_value(value: Any) -> AttrValue:
    """Coerce an attribute value into the schema's ``AttrValue`` shape."""
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple):
        return [str(v) for v in value]
    return str(value)


@dataclass(eq=False)
class SpanRecord:
    name: str
    kind: SpanKindAll
    trace_id: str
    parent: SpanRecord | None
    span_id: str = field(default_factory=new_span_id)
    start_time: datetime = field(default_factory=_now)
    end_time: datetime | None = None
    status: Literal["ok", "error"] = "ok"
    status_message: str | None = None
    attributes: dict[str, AttrValue] = field(default_factory=dict)
    # Root-of-run bookkeeping (``trace()`` spans only): previews set explicitly win.
    is_run: bool = False
    input_explicit: bool = False
    output_explicit: bool = False
    _ended: bool = False

    @property
    def run(self) -> SpanRecord | None:
        """The nearest enclosing ``trace()`` span, if any."""
        s: SpanRecord | None = self
        while s is not None:
            if s.is_run:
                return s
            s = s.parent
        return None

    def set_error(self, exc: BaseException) -> None:
        self.status = "error"
        msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        self.status_message = msg[:STATUS_MESSAGE_MAX_CHARS]

    def set_preview(self, which: Literal["input", "output"], text: str) -> None:
        cut, truncated = truncate(text)
        if which == "input":
            self.attributes[Attr.INPUT_PREVIEW] = cut
            self.attributes[Attr.INPUT_TRUNCATED] = truncated
        else:
            self.attributes[Attr.OUTPUT_PREVIEW] = cut
            self.attributes[Attr.OUTPUT_TRUNCATED] = truncated

    def end(self) -> None:
        """Finish the span and hand it to the exporter (once)."""
        if self._ended:
            return
        self._ended = True
        self.end_time = _now()
        if self.end_time < self.start_time:
            self.end_time = self.start_time
        exp = _exporter
        if exp is None:
            return
        try:
            exp.submit(self.to_dict())
        except Exception:
            log.debug("lucentpad: could not queue span", exc_info=True)

    def to_dict(self) -> SpanDict:
        end = self.end_time or self.start_time
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent.span_id if self.parent is not None else None,
            "name": self.name[:NAME_MAX_CHARS] or "span",
            "kind": self.kind,
            "source": "sdk",
            "start_time": self.start_time.isoformat(),
            "end_time": end.isoformat(),
            "status": self.status,
            "status_message": self.status_message,
            "attributes": dict(self.attributes),
            "events": [],
        }


_current: contextvars.ContextVar[SpanRecord | None] = contextvars.ContextVar(
    "lucentpad_current_span", default=None
)


def current_span() -> SpanRecord | None:
    return _current.get()


def start_span(
    name: str, kind: SpanKindAll, attributes: dict[str, AttrValue] | None = None
) -> SpanRecord:
    """Create a span under the current one (or as a new trace's root). Does not activate it."""
    parent = _current.get()
    rec = SpanRecord(
        name=name,
        kind=kind,
        trace_id=parent.trace_id if parent is not None else new_trace_id(),
        parent=parent,
    )
    cfg = _config
    if cfg is not None and cfg.service_name:
        rec.attributes[Attr.SERVICE_NAME] = cfg.service_name
    if attributes:
        rec.attributes.update(attributes)
    return rec


def note_llm_previews(rec: SpanRecord) -> None:
    """Fold an ended llm span's previews into its run's root (first input, last output)."""
    run = rec.run
    if run is None:
        return
    inp = rec.attributes.get(Attr.INPUT_PREVIEW)
    if isinstance(inp, str) and not run.input_explicit and Attr.INPUT_PREVIEW not in run.attributes:
        run.attributes[Attr.INPUT_PREVIEW] = inp
        run.attributes[Attr.INPUT_TRUNCATED] = bool(rec.attributes.get(Attr.INPUT_TRUNCATED))
    out = rec.attributes.get(Attr.OUTPUT_PREVIEW)
    if isinstance(out, str) and out and not run.output_explicit:
        run.attributes[Attr.OUTPUT_PREVIEW] = out
        run.attributes[Attr.OUTPUT_TRUNCATED] = bool(rec.attributes.get(Attr.OUTPUT_TRUNCATED))


# --------------------------------------------------------------------------- trace()


class Trace:
    """``trace()`` result: a sync and async context manager around the run's root span."""

    def __init__(self, name: str, input: str | None, attributes: dict[str, Any]) -> None:
        self._name = name
        self._input = input
        self._attributes = attributes
        self._rec: SpanRecord | None = None
        self._token: contextvars.Token[SpanRecord | None] | None = None
        self._pending_output: str | None = None
        self.trace_id: str = new_trace_id()

    def _enter(self) -> Self:
        try:
            cfg = _config
            if cfg is None:
                return self
            attrs = {k: clean_value(v) for k, v in self._attributes.items()}
            rec = start_span(self._name, "agent", attrs)
            if rec.parent is None:
                rec.trace_id = self.trace_id
            self.trace_id = rec.trace_id
            rec.is_run = True
            if self._input is not None:
                rec.input_explicit = True
                if cfg.capture_content:
                    rec.set_preview("input", self._input)
            self._rec = rec
            self._token = _current.set(rec)
        except Exception:
            log.debug("lucentpad: trace start failed", exc_info=True)
        return self

    def _exit(self, exc: BaseException | None) -> None:
        rec, token = self._rec, self._token
        self._rec = self._token = None
        if rec is None:
            return
        try:
            if token is not None:
                try:
                    _current.reset(token)
                except ValueError:  # exited in another context: best effort
                    _current.set(rec.parent)
            if exc is not None:
                rec.set_error(exc)
            rec.end()
        except Exception:
            log.debug("lucentpad: trace end failed", exc_info=True)

    def set_output(self, text: str) -> None:
        rec = self._rec
        cfg = _config
        if rec is None or cfg is None:
            return
        rec.output_explicit = True
        if cfg.capture_content:
            rec.set_preview("output", text)
        else:
            rec.attributes.pop(Attr.OUTPUT_PREVIEW, None)
            rec.attributes.pop(Attr.OUTPUT_TRUNCATED, None)

    def __enter__(self) -> Self:
        return self._enter()

    def __exit__(
        self, et: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._exit(exc)

    async def __aenter__(self) -> Self:
        return self._enter()

    async def __aexit__(
        self, et: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._exit(exc)


# --------------------------------------------------------------------------- @span


def _tool_attrs(name: str, kind: str) -> dict[str, AttrValue]:
    if kind != "tool":
        return {}
    return {Attr.GEN_AI_OPERATION: "execute_tool", Attr.GEN_AI_TOOL_NAME: name}


def decorate[F: Callable[..., Any]](func: F, name: str | None, kind: Literal["tool", "agent"]) -> F:
    span_name = name or getattr(func, "__name__", None) or "span"

    def begin() -> tuple[SpanRecord | None, contextvars.Token[SpanRecord | None] | None]:
        if _config is None:
            return None, None
        try:
            rec = start_span(span_name, kind, _tool_attrs(span_name, kind))
            return rec, _current.set(rec)
        except Exception:
            log.debug("lucentpad: span start failed", exc_info=True)
            return None, None

    def finish(
        rec: SpanRecord | None,
        token: contextvars.Token[SpanRecord | None] | None,
        exc: BaseException | None,
    ) -> None:
        if rec is None:
            return
        try:
            if token is not None:
                _current.reset(token)
            if exc is not None:
                rec.set_error(exc)
            rec.end()
        except Exception:
            log.debug("lucentpad: span end failed", exc_info=True)

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            rec, token = begin()
            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                finish(rec, token, exc)
                raise
            finish(rec, token, None)
            return result

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        rec, token = begin()
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            finish(rec, token, exc)
            raise
        finish(rec, token, None)
        return result

    return wrapper  # type: ignore[return-value]


def set_attribute(key: str, value: Any) -> None:
    rec = _current.get()
    if rec is None or _config is None:
        return
    try:
        rec.attributes[str(key)] = clean_value(value)
    except Exception:
        log.debug("lucentpad: set_attribute failed", exc_info=True)
