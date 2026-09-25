"""Tracing for ``anthropic.Anthropic`` / ``AsyncAnthropic``: ``messages.create`` and ``.stream``."""

from __future__ import annotations

import functools
from collections.abc import Iterable
from types import TracebackType
from typing import Any

from ._core import active
from ._llm import LLMCall, block_get, instrument_async_stream, instrument_stream

SYSTEM = "anthropic"
_MARK = "_lucentpad_wrapped"


# --------------------------------------------------------------------------- previews


def _block_text(block: Any, tool_names: dict[str, str]) -> str:
    if isinstance(block, str):
        return block
    kind = block_get(block, "type")
    if kind == "text":
        return str(block_get(block, "text", ""))
    if kind == "tool_result":
        name = tool_names.get(str(block_get(block, "tool_use_id", "")), "tool")
        return f"[tool_result {name}]"
    if kind in ("tool_use", "server_tool_use"):
        return f"[tool_use {block_get(block, 'name', 'tool')}]"
    return f"[{kind}]" if kind else ""


def _content_text(content: Any, tool_names: dict[str, str]) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Iterable):
        parts = [_block_text(b, tool_names) for b in content]
        return "\n".join(p for p in parts if p)
    return ""


def input_preview(messages: Any) -> str | None:
    """The last user message's text; tool results as ``[tool_result <name>]``."""
    if not isinstance(messages, list | tuple):
        return None
    tool_names: dict[str, str] = {}
    for m in messages:
        content = block_get(m, "content")
        if block_get(m, "role") == "assistant" and isinstance(content, list | tuple):
            for b in content:
                if block_get(b, "type") in ("tool_use", "server_tool_use"):
                    tool_names[str(block_get(b, "id", ""))] = str(block_get(b, "name", "tool"))
    for m in reversed(messages):
        if block_get(m, "role") == "user":
            return _content_text(block_get(m, "content"), tool_names)
    return None


def _record_message(call: LLMCall, msg: Any) -> None:
    call.response_model = getattr(msg, "model", None) or call.response_model
    usage = getattr(msg, "usage", None)
    if usage is not None:
        call.input_tokens = getattr(usage, "input_tokens", None)
        call.output_tokens = getattr(usage, "output_tokens", None)
    stop = getattr(msg, "stop_reason", None)
    if stop:
        call.finish_reasons = [str(stop)]
    if call.capture:
        for block in getattr(msg, "content", None) or []:
            text = _block_text(block, {})
            if text:
                call.output.add_block(text)


def _observer(call: LLMCall) -> Any:
    def observe(event: Any) -> bool:
        kind = getattr(event, "type", None)
        if kind == "message_start":
            message = event.message
            call.response_model = getattr(message, "model", None) or call.response_model
            usage = getattr(message, "usage", None)
            if usage is not None:
                call.input_tokens = getattr(usage, "input_tokens", None)
                call.output_tokens = getattr(usage, "output_tokens", None)
        elif kind == "content_block_start" and call.capture:
            block = event.content_block
            btype = getattr(block, "type", None)
            if btype == "text":
                call.output.add_block(getattr(block, "text", "") or "")
            elif btype in ("tool_use", "server_tool_use"):
                call.output.add_block(f"[tool_use {getattr(block, 'name', 'tool')}]")
        elif kind == "content_block_delta" and call.capture:
            delta = event.delta
            if getattr(delta, "type", None) == "text_delta":
                call.output.add(getattr(delta, "text", "") or "")
        elif kind == "message_delta":
            stop = getattr(event.delta, "stop_reason", None)
            if stop:
                call.finish_reasons = [str(stop)]
            usage = getattr(event, "usage", None)
            if usage is not None:
                if getattr(usage, "output_tokens", None) is not None:
                    call.output_tokens = usage.output_tokens
                if getattr(usage, "input_tokens", None) is not None:
                    call.input_tokens = usage.input_tokens
        return True

    return observe


def _new_call(kwargs: dict[str, Any], streaming: bool) -> LLMCall:
    model = kwargs.get("model")
    return LLMCall(
        system=SYSTEM,
        request_model=model if isinstance(model, str) else None,
        streaming=streaming,
        input_preview=lambda: input_preview(kwargs.get("messages")),
    )


# --------------------------------------------------------------------------- wrappers


def _wrap_create(original: Any) -> Any:
    @functools.wraps(original)
    def create(*args: Any, **kwargs: Any) -> Any:
        if active() is None:
            return original(*args, **kwargs)
        streaming = kwargs.get("stream") is True
        try:
            call: LLMCall | None = _new_call(kwargs, streaming)
        except Exception:
            call = None
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            if call is not None:
                call.finish(exc)
            raise
        if call is not None:
            try:
                if streaming:
                    instrument_stream(result, call, _observer(call))
                else:
                    _record_message(call, result)
                    call.finish()
            except Exception:
                call.finish()
        return result

    return create


def _wrap_acreate(original: Any) -> Any:
    @functools.wraps(original)
    async def create(*args: Any, **kwargs: Any) -> Any:
        if active() is None:
            return await original(*args, **kwargs)
        streaming = kwargs.get("stream") is True
        try:
            call: LLMCall | None = _new_call(kwargs, streaming)
        except Exception:
            call = None
        try:
            result = await original(*args, **kwargs)
        except BaseException as exc:
            if call is not None:
                call.finish(exc)
            raise
        if call is not None:
            try:
                if streaming:
                    instrument_async_stream(result, call, _observer(call))
                else:
                    _record_message(call, result)
                    call.finish()
            except Exception:
                call.finish()
        return result

    return create


class _StreamManager:
    """Wraps ``MessageStreamManager``; the span starts at ``__enter__``."""

    def __init__(self, manager: Any, kwargs: dict[str, Any]) -> None:
        self._manager = manager
        self._kwargs = kwargs

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    def __enter__(self) -> Any:
        try:
            call: LLMCall | None = _new_call(self._kwargs, True) if active() else None
        except Exception:
            call = None
        try:
            stream = self._manager.__enter__()
        except BaseException as exc:
            if call is not None:
                call.finish(exc)
            raise
        if call is not None:
            try:
                instrument_stream(stream._raw_stream, call, _observer(call))
            except Exception:
                call.finish()
        return stream

    def __exit__(
        self, et: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> Any:
        return self._manager.__exit__(et, exc, tb)


class _AsyncStreamManager:
    def __init__(self, manager: Any, kwargs: dict[str, Any]) -> None:
        self._manager = manager
        self._kwargs = kwargs

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)

    async def __aenter__(self) -> Any:
        try:
            call: LLMCall | None = _new_call(self._kwargs, True) if active() else None
        except Exception:
            call = None
        try:
            stream = await self._manager.__aenter__()
        except BaseException as exc:
            if call is not None:
                call.finish(exc)
            raise
        if call is not None:
            try:
                instrument_async_stream(stream._raw_stream, call, _observer(call))
            except Exception:
                call.finish()
        return stream

    async def __aexit__(
        self, et: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> Any:
        return await self._manager.__aexit__(et, exc, tb)


def _wrap_stream(original: Any, manager_cls: type[_StreamManager | _AsyncStreamManager]) -> Any:
    @functools.wraps(original)
    def stream(*args: Any, **kwargs: Any) -> Any:
        manager = original(*args, **kwargs)
        return manager_cls(manager, dict(kwargs))

    return stream


def wrap_client(client: Any, *, is_async: bool) -> None:
    messages = client.messages
    if getattr(messages, _MARK, False):
        return
    if is_async:
        messages.create = _wrap_acreate(messages.create)
        messages.stream = _wrap_stream(messages.stream, _AsyncStreamManager)
    else:
        messages.create = _wrap_create(messages.create)
        messages.stream = _wrap_stream(messages.stream, _StreamManager)
    setattr(messages, _MARK, True)
