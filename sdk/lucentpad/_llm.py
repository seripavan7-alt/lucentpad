"""Shared machinery for provider wrappers: one ``kind="llm"`` span per model call, and streams."""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from ._attrs import Attr
from ._core import (
    SpanRecord,
    account_spend,
    active,
    budget_runs,
    budget_stop,
    current_prices,
    current_rules,
    current_rules_async,
    current_span,
    note_llm_previews,
    record_block,
    start_span,
)
from ._errors import GuardrailBlocked
from ._previews import PreviewBuilder
from ._pricing import cost_usd
from ._remote import RuleSet
from .guardrails import check_prompt

log = logging.getLogger("lucentpad")


def safe[T](fn: Callable[[], T], default: T) -> T:
    try:
        return fn()
    except Exception:
        log.debug("lucentpad: instrumentation error", exc_info=True)
        return default


class LLMCall:
    """State of one traced model call. Every method swallows its own errors."""

    def __init__(
        self,
        *,
        system: str,
        request_model: str | None,
        streaming: bool,
        input_preview: Callable[[], str | None],
    ) -> None:
        cfg = active()
        self.capture = bool(cfg and cfg.capture_content)
        self.output = PreviewBuilder()
        self.response_model: str | None = None
        self.input_tokens: int | None = None  # all input, cached included
        self.output_tokens: int | None = None
        self.cache_read_tokens: int | None = None
        self.cache_creation_tokens: int | None = None
        self.finish_reasons: list[str] = []
        self.request_model = request_model
        self._done = False
        self._lock = threading.Lock()
        model = request_model or "unknown"
        attrs: dict[str, Any] = {
            Attr.GEN_AI_SYSTEM: system,
            Attr.GEN_AI_OPERATION: "chat",
            Attr.STREAMING: streaming,
            Attr.CLIENT: "sdk",
        }
        if request_model:
            attrs[Attr.GEN_AI_REQUEST_MODEL] = request_model
        self.rec: SpanRecord = start_span(f"chat {model}", "llm", attrs)
        if self.capture:
            text = safe(input_preview, None)
            if text is not None:
                self.rec.set_preview("input", text)

    def finish(self, exc: BaseException | None = None) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        try:
            a = self.rec.attributes
            if self.response_model:
                a[Attr.GEN_AI_RESPONSE_MODEL] = self.response_model
            if self.input_tokens is not None:
                a[Attr.GEN_AI_INPUT_TOKENS] = int(self.input_tokens)
            if self.output_tokens is not None:
                a[Attr.GEN_AI_OUTPUT_TOKENS] = int(self.output_tokens)
            if self.cache_read_tokens is not None:
                a[Attr.GEN_AI_CACHE_READ_TOKENS] = int(self.cache_read_tokens)
            if self.cache_creation_tokens is not None:
                a[Attr.GEN_AI_CACHE_CREATION_TOKENS] = int(self.cache_creation_tokens)
            if self.finish_reasons:
                a[Attr.GEN_AI_FINISH_REASONS] = list(self.finish_reasons)
            if self.capture and not self.output.empty:
                self.rec.set_preview("output", self.output.text(), overflow=self.output.truncated)
            if exc is not None:
                self.rec.set_error(exc)
            self._account_budget()
            self.rec.finalize()  # redact before the root copies the previews
            note_llm_previews(self.rec)
            self.rec.end()
        except Exception:
            log.debug("lucentpad: llm span end failed", exc_info=True)
            self.rec.end()

    def _account_budget(self) -> None:
        """Estimate this call's cost from the price table and add it to the run's budget."""
        try:
            if not budget_runs(self.rec.parent):
                return
            prices = current_prices()
            if prices is None or (self.input_tokens is None and self.output_tokens is None):
                return
            cost = None
            for model in (self.response_model, self.request_model):
                if model:
                    cost = cost_usd(
                        prices,
                        model,
                        int(self.input_tokens or 0),
                        int(self.output_tokens or 0),
                        int(self.cache_read_tokens or 0),
                        int(self.cache_creation_tokens or 0),
                    )
                    if cost is not None:
                        break
            if cost is not None:
                account_spend(self.rec, cost)
        except Exception:
            log.debug("lucentpad: budget estimate failed", exc_info=True)


# --------------------------------------------------------------------------- before each call


def _check(prompt: Callable[[], str | None], rules: RuleSet | None) -> Exception | None:
    """Budget stop, then prompt rules on the last user message. Returns the exception to raise
    (``BudgetExceeded`` / ``GuardrailBlocked``); never raises itself."""
    try:
        stop = budget_stop(current_span())
        if stop is not None:
            return stop
        if rules is None:
            rules = current_rules()
        if not rules.prompt:
            return None
        text = prompt()
        if not text:
            return None
        block = check_prompt(rules.prompt, text)
        if block is None:
            return None
        record_block(block, {}, input_preview=text)
        return GuardrailBlocked(block.rule, block.reason)
    except Exception:
        log.debug("lucentpad: guardrail check failed", exc_info=True)
        return None


def preflight(prompt: Callable[[], str | None], *, wait: bool = True) -> None:
    """Run before a wrapped model call is sent; raises ``GuardrailBlocked``/``BudgetExceeded``.
    ``wait=False``: never wait for the first rules fetch (sync code on an event loop)."""
    exc = _check(prompt, None if wait else current_rules(wait=False))
    if exc is not None:
        raise exc


async def apreflight(prompt: Callable[[], str | None]) -> None:
    """Async ``preflight``: the one-time wait for the first rules fetch doesn't block the loop."""
    exc = _check(prompt, await current_rules_async())
    if exc is not None:
        raise exc


def instrument_stream(stream: Any, call: LLMCall, observe: Callable[[Any], bool]) -> None:
    """Observe a provider ``Stream`` in place (same object, same chunks, no buffering).

    ``observe(chunk)`` records the chunk and returns False to hide it from the caller (D4).
    The span ends when the stream is exhausted, raises, is closed, or is garbage-collected.
    """
    inner: Iterator[Any] = stream._iterator

    def observed() -> Iterator[Any]:
        try:
            for item in inner:
                keep = True
                try:
                    keep = observe(item)
                except Exception:
                    log.debug("lucentpad: stream observe error", exc_info=True)
                if keep:
                    yield item
        except GeneratorExit:
            call.finish()
            raise
        except BaseException as exc:
            call.finish(exc)
            raise
        finally:
            call.finish()
            close_inner = getattr(inner, "close", None)
            if close_inner is not None:
                close_inner()

    original_close = stream.close

    def close() -> None:
        try:
            original_close()
        finally:
            call.finish()

    stream._iterator = observed()
    stream.close = close
    finalizer = weakref.finalize(stream, call.finish)
    finalizer.atexit = False  # type: ignore[misc]  # typeshed: settable property


def instrument_async_stream(stream: Any, call: LLMCall, observe: Callable[[Any], bool]) -> None:
    """Async twin of ``instrument_stream`` for ``AsyncStream``."""
    inner: AsyncIterator[Any] = stream._iterator

    async def observed() -> AsyncIterator[Any]:
        try:
            async for item in inner:
                keep = True
                try:
                    keep = observe(item)
                except Exception:
                    log.debug("lucentpad: stream observe error", exc_info=True)
                if keep:
                    yield item
        except GeneratorExit:
            call.finish()
            raise
        except BaseException as exc:
            call.finish(exc)
            raise
        finally:
            call.finish()
            aclose = getattr(inner, "aclose", None)
            if aclose is not None:
                await aclose()

    original_close = stream.close

    async def close() -> None:
        try:
            await original_close()
        finally:
            call.finish()

    stream._iterator = observed()
    stream.close = close
    finalizer = weakref.finalize(stream, call.finish)
    finalizer.atexit = False  # type: ignore[misc]  # typeshed: settable property


def block_get(block: Any, key: str, default: Any = None) -> Any:
    """Read a field from a content block that may be a dict (params) or a model (responses)."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)
