"""SDK state, span records, parenting (contextvars), ``trace()`` and ``@span``, guardrails and
budgets."""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import logging
import math
import os
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal, Self

from ._attrs import (
    NAME_MAX_CHARS,
    PREVIEW_MAX_CHARS,
    SPAN_EVENTS_MAX,
    STATUS_MESSAGE_MAX_CHARS,
    Attr,
    EventName,
)
from ._errors import BudgetExceeded, GuardrailBlocked
from ._exporter import Exporter, SpanDict
from ._previews import CAPTURE_CHARS, REDACT_MARGIN, cut
from ._pricing import Price
from ._remote import EMPTY_RULES, REFRESH_INTERVAL, Remote, RuleSet, local_ruleset
from .guardrails import Block, check_tool, redact

log = logging.getLogger("lucentpad")

DEFAULT_ENDPOINT = "http://localhost:8000"
SpanKindAll = Literal["agent", "llm", "tool", "guardrail"]
SpanStatus = Literal["ok", "error", "blocked"]
OnBudget = Literal["alert", "stop"]
AttrValue = str | int | float | bool | list[str]


# --------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class Config:
    endpoint: str
    service_name: str | None
    capture_content: bool
    default_budget_usd: float | None = None


_lock = threading.Lock()
_config: Config | None = None
_exporter: Exporter | None = None
_remote: Remote | None = None
_atexit_registered = False


def _env_disabled() -> bool:
    return os.environ.get("LUCENTPAD_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_budget(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        log.debug("lucentpad: ignoring budget %r", value)
        return None
    if not math.isfinite(f) or f < 0:
        log.debug("lucentpad: ignoring budget %r", value)
        return None
    return f


def configure(
    endpoint: str,
    *,
    service_name: str | None,
    capture_content: bool,
    enabled: bool,
    rules: Any = None,
    default_budget_usd: float | None = None,
    exporter: Exporter | None = None,
    refresh_interval: float = REFRESH_INTERVAL,
) -> None:
    """Implementation of ``lucentpad.init``. ``exporter``/``refresh_interval`` are test seams.

    Raises ``ValueError`` only for an invalid local ``rules`` document (a programming error,
    at startup); nothing else raises."""
    global _config, _exporter, _remote, _atexit_registered
    local = local_ruleset(rules) if rules is not None and enabled else None
    old: Exporter | None
    old_remote: Remote | None
    with _lock:
        old, _exporter, _config = _exporter, None, None
        old_remote, _remote = _remote, None
    if old_remote is not None:
        old_remote.stop()
    if old is not None:
        old.shutdown(timeout=1.0)
    if not enabled or _env_disabled():
        return
    if endpoint == DEFAULT_ENDPOINT:
        endpoint = os.environ.get("LUCENTPAD_ENDPOINT", "").strip() or endpoint
    new = exporter if exporter is not None else Exporter(endpoint)
    remote = Remote(endpoint, new.client, local_rules=local, interval=refresh_interval)
    with _lock:
        _config = Config(endpoint, service_name, capture_content, _clean_budget(default_budget_usd))
        _exporter = new
        _remote = remote
        if not _atexit_registered:
            atexit.register(_atexit_shutdown)
            _atexit_registered = True


def active() -> Config | None:
    return _config


def current_exporter() -> Exporter | None:
    return _exporter


def current_remote() -> Remote | None:
    return _remote


def current_rules(*, wait: bool = True) -> RuleSet:
    """The active rules; the first call may wait (bounded) for the first fetch. Never raises."""
    remote = _remote
    try:
        return remote.rules(wait=wait) if remote is not None else EMPTY_RULES
    except Exception:
        return EMPTY_RULES


async def current_rules_async() -> RuleSet:
    remote = _remote
    try:
        return await remote.arules() if remote is not None else EMPTY_RULES
    except Exception:
        return EMPTY_RULES


def current_prices() -> Mapping[str, Price] | None:
    remote = _remote
    return remote.prices() if remote is not None else None


def flush(timeout: float) -> bool:
    exp = _exporter
    if exp is None:
        return True
    try:
        return exp.flush(timeout)
    except Exception:
        return False


def shutdown(timeout: float = 2.0) -> None:
    global _config, _exporter, _remote
    with _lock:
        exp, _exporter, _config = _exporter, None, None
        remote, _remote = _remote, None
    if remote is not None:
        try:
            remote.stop()
        except Exception:
            log.debug("lucentpad: shutdown error", exc_info=True)
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


_PREVIEW_KEYS = {
    Attr.INPUT_PREVIEW: Attr.INPUT_TRUNCATED,
    Attr.OUTPUT_PREVIEW: Attr.OUTPUT_TRUNCATED,
}


@dataclass(eq=False)
class SpanRecord:
    name: str
    kind: SpanKindAll
    trace_id: str
    parent: SpanRecord | None
    span_id: str = field(default_factory=new_span_id)
    start_time: datetime = field(default_factory=_now)
    end_time: datetime | None = None
    status: SpanStatus = "ok"
    status_message: str | None = None
    attributes: dict[str, AttrValue] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    # Root-of-run bookkeeping (``trace()`` spans only): previews set explicitly win.
    is_run: bool = False
    input_explicit: bool = False
    output_explicit: bool = False
    # Run budget (``trace(budget_usd=...)``), spend estimated from the price table.
    budget_usd: float | None = None
    on_budget: OnBudget = "alert"
    spent_usd: float = 0.0
    budget_alerted: bool = False
    budget_stopped: bool = False
    _finalized: bool = False
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
        self.status_message = msg[: STATUS_MESSAGE_MAX_CHARS + REDACT_MARGIN]  # cut at finalize

    def set_preview(
        self, which: Literal["input", "output"], text: str, overflow: bool = False
    ) -> None:
        """Store a preview; it is redacted and cut to ``PREVIEW_MAX_CHARS`` at ``finalize``."""
        key = Attr.INPUT_PREVIEW if which == "input" else Attr.OUTPUT_PREVIEW
        self.attributes[key] = text[:CAPTURE_CHARS]
        self.attributes[_PREVIEW_KEYS[key]] = overflow or len(text) > PREVIEW_MAX_CHARS

    def add_event(
        self, name: str, attributes: dict[str, AttrValue], time: datetime | None = None
    ) -> None:
        if len(self.events) >= SPAN_EVENTS_MAX:
            return
        when = time or _now()
        self.events.append({"name": name, "time": when.isoformat(), "attributes": attributes})

    def finalize(self) -> None:
        """Redact every string the span carries (D20) and cut previews to size; add one
        ``lucentpad.redaction`` event per kind replaced (kind, count; never the value). Once."""
        if self._finalized:
            return
        self._finalized = True
        counts: dict[str, int] = {}

        def clean(s: str) -> str:
            r = redact(s)
            for kind, n in r.counts.items():
                counts[kind] = counts.get(kind, 0) + n
            return r.text

        attrs = self.attributes
        for key, value in list(attrs.items()):
            if isinstance(value, str):
                new = clean(value)
                flag = _PREVIEW_KEYS.get(key)
                if flag is not None:
                    new, was_cut = cut(new)
                    if was_cut:
                        attrs[flag] = True
                attrs[key] = new
            elif isinstance(value, list):
                attrs[key] = [clean(v) if isinstance(v, str) else v for v in value]
        if self.status_message is not None:
            self.status_message = clean(self.status_message)[:STATUS_MESSAGE_MAX_CHARS]
        for kind, n in counts.items():
            self.add_event(
                EventName.REDACTION, {Attr.REDACTION_KIND: kind, Attr.REDACTION_COUNT: n}
            )

    def end(self) -> None:
        """Finish the span and hand it to the exporter (once)."""
        if self._ended:
            return
        self._ended = True
        self.end_time = _now()
        if self.end_time < self.start_time:
            self.end_time = self.start_time
        try:
            self.finalize()
        except Exception:  # never export unredacted text
            log.debug("lucentpad: redaction failed; dropping previews", exc_info=True)
            for key, flag in _PREVIEW_KEYS.items():
                self.attributes.pop(key, None)
                self.attributes.pop(flag, None)
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
            "events": list(self.events),
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
    """Fold an ended llm span's previews into its run's root (first input, last output).
    Call after ``rec.finalize()`` so the root only ever sees redacted text."""
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


# --------------------------------------------------------------------------- guardrails


def record_block(
    block: Block, attributes: dict[str, AttrValue], input_preview: str | None = None
) -> None:
    """Record a block as its own ``kind="guardrail"`` span (status ``blocked``) under the
    current span, with a ``lucentpad.guardrail.block`` event. Never raises."""
    try:
        rec = start_span(
            f"guardrail {block.rule}",
            "guardrail",
            {
                Attr.CLIENT: "sdk",
                Attr.GUARDRAIL_RULE: block.rule,
                Attr.GUARDRAIL_REASON: block.reason,
                **attributes,
            },
        )
        rec.status = "blocked"
        rec.status_message = block.reason
        rec.add_event(EventName.GUARDRAIL_BLOCK, {Attr.GUARDRAIL_RULE: block.rule}, rec.start_time)
        cfg = _config
        if input_preview is not None and cfg is not None and cfg.capture_content:
            rec.set_preview("input", input_preview)
        rec.finalize()
        note_llm_previews(rec)
        rec.end()
    except Exception:
        log.debug("lucentpad: could not record guardrail span", exc_info=True)


# --------------------------------------------------------------------------- budgets

_budget_lock = threading.Lock()


def budget_runs(rec: SpanRecord | None) -> list[SpanRecord]:
    """``rec`` and its ancestors that are runs with a budget, innermost first."""
    out: list[SpanRecord] = []
    s = rec
    while s is not None:
        if s.is_run and s.budget_usd is not None:
            out.append(s)
        s = s.parent
    return out


def budget_stop(parent: SpanRecord | None) -> BudgetExceeded | None:
    """The error to raise when an enclosing ``on_budget="stop"`` run is over its budget."""
    for run in budget_runs(parent):
        if run.budget_stopped and run.budget_usd is not None:
            return BudgetExceeded(run.budget_usd, round(run.spent_usd, 6))
    return None


def account_spend(rec: SpanRecord, cost_usd: float) -> None:
    """Add an llm call's estimated cost to its runs; alert on the call that crosses a budget."""
    with _budget_lock:
        for run in budget_runs(rec.parent):
            limit = run.budget_usd
            if limit is None:
                continue
            run.spent_usd += cost_usd
            if not run.budget_alerted and run.spent_usd > limit:
                run.budget_alerted = True
                rec.add_event(
                    EventName.BUDGET_ALERT,
                    {
                        Attr.BUDGET_LIMIT_USD: limit,
                        Attr.BUDGET_SPENT_USD: round(run.spent_usd, 6),
                        Attr.BUDGET_SCOPE: "run",
                    },
                )
                if run.on_budget == "stop":
                    run.budget_stopped = True


# --------------------------------------------------------------------------- trace()


class Trace:
    """``trace()`` result: a sync and async context manager around the run's root span."""

    def __init__(
        self,
        name: str,
        input: str | None,
        attributes: dict[str, Any],
        budget_usd: float | None = None,
        on_budget: str = "alert",
    ) -> None:
        self._name = name
        self._input = input
        self._attributes = attributes
        self._budget_usd = budget_usd
        self._on_budget: OnBudget = "stop" if on_budget == "stop" else "alert"
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
            enclosing = rec.parent.run if rec.parent is not None else None
            rec.is_run = True
            if self._budget_usd is not None:
                rec.budget_usd = _clean_budget(self._budget_usd)
            elif enclosing is None:  # the default applies to top-level runs only
                rec.budget_usd = cfg.default_budget_usd
            rec.on_budget = self._on_budget
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


def _signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def bind_arguments(
    sig: inspect.Signature | None, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any] | None:
    """The call's arguments by parameter name (defaults applied, ``**kwargs`` merged in)."""
    if sig is None:
        return dict(kwargs) if not args else None
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        return None  # the call itself will raise
    bound.apply_defaults()
    out: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        if sig.parameters[name].kind is inspect.Parameter.VAR_KEYWORD and isinstance(value, dict):
            for k, v in value.items():
                out.setdefault(k, v)
        else:
            out[name] = value
    return out


def check_tool_call(
    rules: RuleSet,
    tool: str,
    sig: inspect.Signature | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> GuardrailBlocked | None:
    """Tool rules for ``tool`` on this call's arguments; records the block. Never raises."""
    try:
        tool_rules = rules.tool.get(tool)
        if not tool_rules:
            return None
        bound = bind_arguments(sig, args, kwargs)
        if bound is None:
            return None
        block = check_tool(tool_rules, tool, bound)
        if block is None:
            return None
        record_block(block, {Attr.GEN_AI_TOOL_NAME: tool})
        return GuardrailBlocked(block.rule, block.reason)
    except Exception:
        log.debug("lucentpad: tool guardrail check failed", exc_info=True)
        return None


def decorate[F: Callable[..., Any]](func: F, name: str | None, kind: Literal["tool", "agent"]) -> F:
    span_name = name or getattr(func, "__name__", None) or "span"
    sig = _signature(func) if kind == "tool" else None

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

    guarded = kind == "tool"

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if guarded and _config is not None:
                blocked = check_tool_call(await current_rules_async(), span_name, sig, args, kwargs)
                if blocked is not None:
                    raise blocked
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
        if guarded and _config is not None:
            blocked = check_tool_call(current_rules(), span_name, sig, args, kwargs)
            if blocked is not None:
                raise blocked
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
