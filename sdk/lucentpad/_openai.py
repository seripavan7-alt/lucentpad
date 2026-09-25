"""Tracing for ``openai.OpenAI`` / ``AsyncOpenAI``: ``chat.completions.create``, plain and streamed.

D4: token counts only arrive on a stream when ``stream_options.include_usage`` is set. When the
caller didn't set it, the wrapper sets it and hides the extra usage-only chunk, so the caller's
stream is unchanged.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from typing import Any

from ._core import active
from ._llm import LLMCall, block_get, instrument_async_stream, instrument_stream

SYSTEM = "openai"
_MARK = "_lucentpad_wrapped"


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Iterable):
        parts: list[str] = []
        for part in content:
            kind = block_get(part, "type")
            if kind == "text":
                parts.append(str(block_get(part, "text", "")))
            elif kind:
                parts.append(f"[{kind}]")
        return "\n".join(p for p in parts if p)
    return ""


def input_preview(messages: Any) -> str | None:
    """The last user message's text, or ``[tool_result <name>]`` when tool results come last."""
    if not isinstance(messages, list | tuple):
        return None
    tool_names: dict[str, str] = {}
    for m in messages:
        if block_get(m, "role") == "assistant":
            for tc in block_get(m, "tool_calls") or []:
                fn = block_get(tc, "function")
                tool_names[str(block_get(tc, "id", ""))] = str(block_get(fn, "name", "tool"))
    tail: list[str] = []
    for m in reversed(messages):
        role = block_get(m, "role")
        if role == "tool":
            name = tool_names.get(str(block_get(m, "tool_call_id")), "tool")
            tail.append(f"[tool_result {name}]")
            continue
        if tail:
            break
        if role == "user":
            return _content_text(block_get(m, "content"))
    if tail:
        return "\n".join(reversed(tail))
    return None


def _record_completion(call: LLMCall, completion: Any) -> None:
    call.response_model = getattr(completion, "model", None) or call.response_model
    usage = getattr(completion, "usage", None)
    if usage is not None:
        call.input_tokens = getattr(usage, "prompt_tokens", None)
        call.output_tokens = getattr(usage, "completion_tokens", None)
    choices = getattr(completion, "choices", None) or []
    call.finish_reasons = [
        str(c.finish_reason) for c in choices if getattr(c, "finish_reason", None)
    ]
    if call.capture and choices:
        message = getattr(choices[0], "message", None)
        text = _content_text(getattr(message, "content", None))
        if text:
            call.output.add_block(text)
        for tc in getattr(message, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            call.output.add_block(f"[tool_use {getattr(fn, 'name', 'tool')}]")


def _observer(call: LLMCall, hide_usage_chunk: bool) -> Any:
    finish: dict[int, str] = {}

    def observe(chunk: Any) -> bool:
        call.response_model = getattr(chunk, "model", None) or call.response_model
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            call.input_tokens = getattr(usage, "prompt_tokens", None)
            call.output_tokens = getattr(usage, "completion_tokens", None)
        choices = getattr(chunk, "choices", None) or []
        for choice in choices:
            index = getattr(choice, "index", 0)
            if getattr(choice, "finish_reason", None):
                finish[index] = str(choice.finish_reason)
                call.finish_reasons = [finish[i] for i in sorted(finish)]
            if index != 0 or not call.capture:
                continue
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if content:
                call.output.add(content)
            for tc in getattr(delta, "tool_calls", None) or []:
                name = getattr(getattr(tc, "function", None), "name", None)
                if name:
                    call.output.add_block(f"[tool_use {name}]")
        return not (hide_usage_chunk and not choices and usage is not None)

    return observe


def _prepare(kwargs: dict[str, Any]) -> tuple[bool, bool]:
    """Return (streaming, injected_include_usage); mutates ``kwargs`` for D4."""
    streaming = kwargs.get("stream") is True
    if not streaming:
        return False, False
    options = kwargs.get("stream_options")
    if isinstance(options, dict):
        if "include_usage" in options:
            return True, False
        kwargs["stream_options"] = {**options, "include_usage": True}
        return True, True
    if options is None or type(options).__name__ in ("Omit", "NotGiven"):
        kwargs["stream_options"] = {"include_usage": True}
        return True, True
    return True, False


def _new_call(kwargs: dict[str, Any], streaming: bool) -> LLMCall:
    model = kwargs.get("model")
    return LLMCall(
        system=SYSTEM,
        request_model=model if isinstance(model, str) else None,
        streaming=streaming,
        input_preview=lambda: input_preview(kwargs.get("messages")),
    )


def _begin(kwargs: dict[str, Any]) -> tuple[dict[str, Any], LLMCall | None, bool, bool]:
    try:
        new_kwargs = dict(kwargs)
        streaming, injected = _prepare(new_kwargs)
        return new_kwargs, _new_call(new_kwargs, streaming), streaming, injected
    except Exception:
        return kwargs, None, kwargs.get("stream") is True, False


def _wrap_create(original: Any) -> Any:
    @functools.wraps(original)
    def create(*args: Any, **kwargs: Any) -> Any:
        if active() is None:
            return original(*args, **kwargs)
        kwargs, call, streaming, injected = _begin(kwargs)
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            if call is not None:
                call.finish(exc)
            raise
        if call is not None:
            try:
                if streaming:
                    instrument_stream(result, call, _observer(call, injected))
                else:
                    _record_completion(call, result)
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
        kwargs, call, streaming, injected = _begin(kwargs)
        try:
            result = await original(*args, **kwargs)
        except BaseException as exc:
            if call is not None:
                call.finish(exc)
            raise
        if call is not None:
            try:
                if streaming:
                    instrument_async_stream(result, call, _observer(call, injected))
                else:
                    _record_completion(call, result)
                    call.finish()
            except Exception:
                call.finish()
        return result

    return create


def wrap_client(client: Any, *, is_async: bool) -> None:
    completions = client.chat.completions
    if getattr(completions, _MARK, False):
        return
    completions.create = (_wrap_acreate if is_async else _wrap_create)(completions.create)
    setattr(completions, _MARK, True)
