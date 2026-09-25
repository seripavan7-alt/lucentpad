"""Shared machinery for provider wrappers: one ``kind="llm"`` span per model call, and streams."""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from ._attrs import Attr
from ._core import SpanRecord, active, note_llm_previews, start_span
from ._previews import PreviewBuilder

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
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.finish_reasons: list[str] = []
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
            if self.finish_reasons:
                a[Attr.GEN_AI_FINISH_REASONS] = list(self.finish_reasons)
            if self.capture and not self.output.empty:
                a[Attr.OUTPUT_PREVIEW] = self.output.text()
                a[Attr.OUTPUT_TRUNCATED] = self.output.truncated
            if exc is not None:
                self.rec.set_error(exc)
            note_llm_previews(self.rec)
            self.rec.end()
        except Exception:
            log.debug("lucentpad: llm span end failed", exc_info=True)


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
