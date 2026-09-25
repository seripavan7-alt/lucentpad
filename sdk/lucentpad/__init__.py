"""LucentPad SDK: trace every LLM call your Python agent makes.

    import lucentpad
    from anthropic import Anthropic

    lucentpad.init(service_name="support-agent")
    client = lucentpad.wrap(Anthropic())

    with lucentpad.trace("refund request"):
        client.messages.create(...)

Spans are batched and exported in the background to ``POST /v1/spans``. The SDK never raises
into, blocks or slows the host agent, and never reads or exports API keys or request headers.

Environment: ``LUCENTPAD_ENDPOINT`` (overrides the default endpoint) and ``LUCENTPAD_DISABLED=1``
(every call becomes a no-op).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Any, Literal, Protocol, cast, overload

from . import _core

__all__ = ["flush", "init", "set_attribute", "shutdown", "span", "trace", "wrap"]

SpanKind = Literal["tool", "agent"]


class TraceContext(
    AbstractContextManager["TraceContext"], AbstractAsyncContextManager["TraceContext"], Protocol
):
    """Returned by ``trace()``: usable as ``with`` or ``async with``."""

    trace_id: str

    def set_output(self, text: str) -> None:
        """Set the run's output preview explicitly (otherwise: the last llm call's text)."""
        ...


def init(
    endpoint: str = "http://localhost:8000",
    *,
    service_name: str | None = None,
    capture_content: bool = True,
    enabled: bool = True,
) -> None:
    """Configure the SDK and start the background exporter. Call once, at startup.

    ``endpoint`` is the LucentPad API (env ``LUCENTPAD_ENDPOINT`` overrides the default).
    ``capture_content=False`` records no prompt/response previews. ``enabled=False`` (or env
    ``LUCENTPAD_DISABLED=1``) makes every other call a no-op.
    """
    _core.configure(
        endpoint, service_name=service_name, capture_content=capture_content, enabled=enabled
    )


def wrap[ClientT](client: ClientT) -> ClientT:
    """Return the same client, traced: every model call becomes a ``kind="llm"`` span.

    Supports ``anthropic.Anthropic`` / ``AsyncAnthropic`` (``messages.create`` plain and
    streaming, ``messages.stream``) and ``openai.OpenAI`` / ``AsyncOpenAI``
    (``chat.completions.create`` plain and streaming). Other objects are returned unchanged.
    """
    try:
        _wrap(client)
    except Exception:
        _core.log.debug("lucentpad: could not wrap client", exc_info=True)
    return client


def _wrap(client: object) -> None:
    try:
        import anthropic
    except ImportError:
        pass
    else:
        from . import _anthropic

        if isinstance(client, anthropic.AsyncAnthropic):
            return _anthropic.wrap_client(client, is_async=True)
        if isinstance(client, anthropic.Anthropic):
            return _anthropic.wrap_client(client, is_async=False)
    try:
        import openai
    except ImportError:
        return
    from . import _openai

    if isinstance(client, openai.AsyncOpenAI):
        return _openai.wrap_client(client, is_async=True)
    if isinstance(client, openai.OpenAI):
        return _openai.wrap_client(client, is_async=False)


def trace(name: str, *, input: str | None = None, **attributes: Any) -> TraceContext:
    """Group a run under one root ``kind="agent"`` span (sync or async context manager).

    ``input`` sets the run's input preview (otherwise: the first llm call's user message).
    Extra keyword arguments become span attributes.
    """
    return cast(TraceContext, _core.Trace(name, input, attributes))


@overload
def span[F: Callable[..., Any]](func: F, /) -> F: ...
@overload
def span[F: Callable[..., Any]](
    *, name: str | None = None, kind: SpanKind = "tool"
) -> Callable[[F], F]: ...
def span[F: Callable[..., Any]](
    func: F | None = None, /, *, name: str | None = None, kind: SpanKind = "tool"
) -> F | Callable[[F], F]:
    """Decorator: each call of the (sync or async) function becomes a child span.

    Use bare (``@lucentpad.span``) or with options (``@lucentpad.span(name="lookup")``).
    """
    if func is not None:
        return _core.decorate(func, None, "tool")

    def decorator(f: F) -> F:
        return _core.decorate(f, name, kind)

    return decorator


def set_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current span (the innermost ``@span`` or ``trace()``).

    Values are str, int, float, bool or a list of str (anything else is stored as its ``str``).
    A no-op outside a span or when the SDK is disabled.
    """
    _core.set_attribute(key, value)


def flush(timeout: float = 2.0) -> bool:
    """Export buffered spans now; True if everything was sent within ``timeout`` seconds."""
    return _core.flush(timeout)


def shutdown() -> None:
    """Flush and stop the exporter. Also runs at interpreter exit."""
    _core.shutdown()
