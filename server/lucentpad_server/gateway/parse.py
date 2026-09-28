"""Reading requests and responses on the side: SSE, usage, previews. Never touches the bytes
that go to the client, and never raises into the relay (callers guard ``feed``).

``ResponseInfo`` collects what a span needs; ``StreamParser`` fills it from a stream chunk by
chunk; ``parse_json_response`` fills it from a whole body; ``request_info`` reads the request.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from typing import Any

from lucentpad_server.schema import PREVIEW_MAX_CHARS

MAX_LINE_BYTES = 4 * 1024 * 1024
"""A single SSE line longer than this stops parsing (usage becomes unknown); relay goes on."""


class PreviewBuilder:
    """Collects text up to the cap, with a truncated flag."""

    __slots__ = ("_limit", "_parts", "_size", "truncated")

    def __init__(self, limit: int = PREVIEW_MAX_CHARS) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def add(self, text: str) -> None:
        if not text or self.truncated:
            return
        room = self._limit - self._size
        if len(text) > room:
            text = text[:room]
            self.truncated = True
        if text:
            self._parts.append(text)
            self._size += len(text)

    def add_block(self, text: str) -> None:
        if self._size:
            self.add("\n")
        self.add(text)

    @property
    def empty(self) -> bool:
        return self._size == 0 and not self.truncated

    def text(self) -> str:
        return "".join(self._parts)


@dataclass
class ResponseInfo:
    model: str | None = None
    input_tokens: int | None = None
    """All input tokens, cached included (OpenTelemetry's convention)."""
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    finish_reasons: list[str] = field(default_factory=list)
    output: PreviewBuilder = field(default_factory=PreviewBuilder)
    error: str | None = None
    """An error the upstream reported in the body (``type: message``)."""


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _error_text(err: Any) -> str:
    err = _dict(err)
    kind = err.get("type") or err.get("code") or "error"
    message = err.get("message") or ""
    return f"{kind}: {message}"[:500] if message else str(kind)[:500]


# --------------------------------------------------------------------------- Anthropic


def _anthropic_usage(info: ResponseInfo, usage: dict[str, Any]) -> None:
    """Anthropic reports uncached input apart from cache reads and writes; sum them."""
    parts = [
        _int(usage.get(k))
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    ]
    if all(p is None for p in parts):
        return
    info.input_tokens = sum(p or 0 for p in parts)
    info.cache_read_tokens = parts[1]
    info.cache_creation_tokens = parts[2]


def _anthropic_event(info: ResponseInfo, data: dict[str, Any]) -> None:
    kind = data.get("type")
    if kind == "message_start":
        msg = _dict(data.get("message"))
        if isinstance(msg.get("model"), str):
            info.model = msg["model"]
        usage = _dict(msg.get("usage"))
        _anthropic_usage(info, usage)
        info.output_tokens = _int(usage.get("output_tokens"))
    elif kind == "content_block_start":
        block = _dict(data.get("content_block"))
        btype = block.get("type")
        if btype == "text":
            info.output.add_block(str(block.get("text") or ""))
        elif btype in ("tool_use", "server_tool_use"):
            info.output.add_block(f"[tool_use {block.get('name', 'tool')}]")
    elif kind == "content_block_delta":
        delta = _dict(data.get("delta"))
        if delta.get("type") == "text_delta":
            info.output.add(str(delta.get("text") or ""))
    elif kind == "message_delta":
        delta = _dict(data.get("delta"))
        if isinstance(delta.get("stop_reason"), str):
            info.finish_reasons = [delta["stop_reason"]]
        usage = _dict(data.get("usage"))
        out = _int(usage.get("output_tokens"))
        if out is not None:
            info.output_tokens = out
        _anthropic_usage(info, usage)
    elif kind == "error":
        info.error = _error_text(data.get("error"))


def _anthropic_message(info: ResponseInfo, data: dict[str, Any]) -> None:
    if data.get("type") == "error":
        info.error = _error_text(data.get("error"))
        return
    if isinstance(data.get("model"), str):
        info.model = data["model"]
    usage = _dict(data.get("usage"))
    _anthropic_usage(info, usage)
    info.output_tokens = _int(usage.get("output_tokens"))
    if isinstance(data.get("stop_reason"), str):
        info.finish_reasons = [data["stop_reason"]]
    for block in data.get("content") or []:
        block = _dict(block)
        if block.get("type") == "text":
            info.output.add_block(str(block.get("text") or ""))
        elif block.get("type") in ("tool_use", "server_tool_use"):
            info.output.add_block(f"[tool_use {block.get('name', 'tool')}]")


# --------------------------------------------------------------------------- OpenAI


def _openai_usage(info: ResponseInfo, usage: Any) -> None:
    usage = _dict(usage)
    if usage:
        info.input_tokens = _int(usage.get("prompt_tokens"))  # cached included
        info.output_tokens = _int(usage.get("completion_tokens"))
        cached = _int(_dict(usage.get("prompt_tokens_details")).get("cached_tokens"))
        if cached:
            info.cache_read_tokens = cached


def _openai_chunk(info: ResponseInfo, data: dict[str, Any], seen_tools: set[int]) -> None:
    if "error" in data and not data.get("choices"):
        info.error = _error_text(data.get("error"))
        return
    if isinstance(data.get("model"), str) and data["model"]:
        info.model = data["model"]
    _openai_usage(info, data.get("usage"))
    for choice in data.get("choices") or []:
        choice = _dict(choice)
        if choice.get("index", 0) != 0:
            continue
        delta = _dict(choice.get("delta"))
        content = delta.get("content")
        if isinstance(content, str):
            if info.output.empty:
                info.output.add_block(content)
            else:
                info.output.add(content)
        for tc in delta.get("tool_calls") or []:
            tc = _dict(tc)
            idx = tc.get("index", 0)
            name = _dict(tc.get("function")).get("name")
            if isinstance(idx, int) and idx not in seen_tools and name:
                seen_tools.add(idx)
                info.output.add_block(f"[tool_use {name}]")
        if isinstance(choice.get("finish_reason"), str):
            info.finish_reasons = [choice["finish_reason"]]


def _openai_completion(info: ResponseInfo, data: dict[str, Any]) -> None:
    if "error" in data and not data.get("choices"):
        info.error = _error_text(data.get("error"))
        return
    if isinstance(data.get("model"), str):
        info.model = data["model"]
    _openai_usage(info, data.get("usage"))
    reasons: list[str] = []
    for choice in data.get("choices") or []:
        choice = _dict(choice)
        if isinstance(choice.get("finish_reason"), str):
            reasons.append(choice["finish_reason"])
        if choice.get("index", 0) != 0:
            continue
        msg = _dict(choice.get("message"))
        if isinstance(msg.get("content"), str):
            info.output.add_block(msg["content"])
        for tc in msg.get("tool_calls") or []:
            name = _dict(_dict(tc).get("function")).get("name", "tool")
            info.output.add_block(f"[tool_use {name}]")
    info.finish_reasons = reasons


# --------------------------------------------------------------------------- streams


class _Inflater:
    """Undoes gzip/deflate on the side copy when the upstream compressed anyway."""

    def __init__(self, encoding: str) -> None:
        wbits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        self._z = zlib.decompressobj(wbits)

    def __call__(self, chunk: bytes) -> bytes:
        return self._z.decompress(chunk)


class StreamParser:
    """Incremental SSE parser feeding a ``ResponseInfo``. ``feed`` never raises; on anything
    unexpected it stops parsing (``ok`` becomes False) and the relay is unaffected."""

    def __init__(self, provider: str, content_encoding: str = "") -> None:
        self.info = ResponseInfo()
        self.ok = True
        self._provider = provider
        self._buf = b""
        self._data: list[str] = []
        self._seen_tools: set[int] = set()
        enc = content_encoding.strip().lower()
        self._inflate: _Inflater | None = None
        if enc in ("gzip", "deflate"):
            self._inflate = _Inflater(enc)
        elif enc and enc != "identity":
            self.ok = False  # br/zstd: relayed untouched, just not parsed

    def feed(self, chunk: bytes) -> None:
        if not self.ok:
            return
        try:
            if self._inflate is not None:
                chunk = self._inflate(chunk)
            self._buf += chunk
            while True:
                nl = self._buf.find(b"\n")
                if nl < 0:
                    break
                line = self._buf[:nl]
                self._buf = self._buf[nl + 1 :]
                self._line(line.rstrip(b"\r").decode("utf-8", "replace"))
            if len(self._buf) > MAX_LINE_BYTES:
                self.ok = False
                self._buf = b""
        except Exception:
            self.ok = False
            self._buf = b""

    def close(self) -> None:
        """End of stream: dispatch a last event without a trailing blank line."""
        if self.ok and self._buf:
            self.feed(b"\n")
        if self.ok and self._data:
            self._dispatch()

    def _line(self, line: str) -> None:
        if not line:
            if self._data:
                self._dispatch()
            return
        if line.startswith(":"):
            return
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)

    def _dispatch(self) -> None:
        payload = "\n".join(self._data)
        self._data = []
        if not payload or payload == "[DONE]":
            return
        try:
            data = json.loads(payload)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if self._provider == "anthropic":
            _anthropic_event(self.info, data)
        else:
            _openai_chunk(self.info, data, self._seen_tools)


def decode_body(body: bytes, content_encoding: str) -> bytes | None:
    enc = content_encoding.strip().lower()
    if not enc or enc == "identity":
        return body
    if enc in ("gzip", "deflate"):
        try:
            return _Inflater(enc)(body)
        except zlib.error:
            return None
    return None


def parse_json_response(provider: str, body: bytes, content_encoding: str = "") -> ResponseInfo:
    info = ResponseInfo()
    raw = decode_body(body, content_encoding)
    if not raw:
        return info
    try:
        data = json.loads(raw)
    except ValueError:
        return info
    if not isinstance(data, dict):
        return info
    if provider == "anthropic":
        _anthropic_message(info, data)
    else:
        _openai_completion(info, data)
    return info


# --------------------------------------------------------------------------- requests


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else default


def _block_text(block: Any, tool_names: dict[str, str]) -> str:
    if isinstance(block, str):
        return block
    kind = _get(block, "type")
    if kind == "text":
        return str(_get(block, "text", ""))
    if kind == "tool_result":
        return f"[tool_result {tool_names.get(str(_get(block, 'tool_use_id', '')), 'tool')}]"
    if kind in ("tool_use", "server_tool_use"):
        return f"[tool_use {_get(block, 'name', 'tool')}]"
    return f"[{kind}]" if kind else ""


def _content_text(content: Any, tool_names: dict[str, str]) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p for p in (_block_text(b, tool_names) for b in content) if p)
    return ""


def anthropic_input_preview(messages: Any) -> str | None:
    """The last user message's text; tool results as ``[tool_result <name>]``."""
    if not isinstance(messages, list):
        return None
    tool_names: dict[str, str] = {}
    for m in messages:
        content = _get(m, "content")
        if _get(m, "role") == "assistant" and isinstance(content, list):
            for b in content:
                if _get(b, "type") in ("tool_use", "server_tool_use"):
                    tool_names[str(_get(b, "id", ""))] = str(_get(b, "name", "tool"))
    for m in reversed(messages):
        if _get(m, "role") == "user":
            return _content_text(_get(m, "content"), tool_names)
    return None


def openai_input_preview(messages: Any) -> str | None:
    """The last user message's text, or ``[tool_result <name>]`` when tool results come last."""
    if not isinstance(messages, list):
        return None
    tool_names: dict[str, str] = {}
    for m in messages:
        if _get(m, "role") == "assistant":
            for tc in _get(m, "tool_calls") or []:
                name = _get(_get(tc, "function"), "name", "tool")
                tool_names[str(_get(tc, "id", ""))] = str(name)
    tail: list[str] = []
    for m in reversed(messages):
        role = _get(m, "role")
        if role == "tool":
            tail.append(f"[tool_result {tool_names.get(str(_get(m, 'tool_call_id')), 'tool')}]")
            continue
        if tail:
            break
        if role == "user":
            return _content_text(_get(m, "content"), {})
    return "\n".join(reversed(tail)) if tail else None


@dataclass
class RequestInfo:
    model: str | None = None
    stream: bool = False
    input_preview: str | None = None


def parse_request(provider: str, body: bytes, *, previews: bool = True) -> tuple[Any, RequestInfo]:
    """The parsed JSON (or None) and what a span needs from it."""
    info = RequestInfo()
    try:
        data = json.loads(body) if body else None
    except ValueError:
        return None, info
    if not isinstance(data, dict):
        return data, info
    if isinstance(data.get("model"), str):
        info.model = data["model"]
    info.stream = data.get("stream") is True
    if previews:
        messages = data.get("messages")
        info.input_preview = (
            anthropic_input_preview(messages)
            if provider == "anthropic"
            else openai_input_preview(messages)
        )
    return data, info


def _text_parts(content: Any) -> str:
    """Only what the user typed: string content and ``text`` blocks (no tool results)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b if isinstance(b, str) else str(_get(b, "text", ""))
            for b in content
            if isinstance(b, str) or _get(b, "type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def last_user_text(data: Any) -> str | None:
    """The text of the request's last ``user`` message (Anthropic and OpenAI shapes alike),
    for prompt rules. Tool results are left out; None when there is no user message."""
    messages = _get(data, "messages")
    if not isinstance(messages, list):
        return None
    for m in reversed(messages):
        if _get(m, "role") == "user":
            return _text_parts(_get(m, "content"))
    return None
