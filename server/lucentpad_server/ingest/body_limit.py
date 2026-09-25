"""ASGI middleware: answer 413 when a request body to a limited path exceeds a byte limit.

Checked on ``Content-Length`` up front and again while reading (chunked bodies), before the
body is parsed, so an oversized batch never reaches JSON decoding.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BODY_ENV = "LUCENTPAD_INGEST_MAX_BODY_BYTES"
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024
"""10 MiB: a full 1 000-span batch with 2 000-char input and output previews on every span
is about 5 MB of JSON, so this leaves headroom for escaping and large attribute maps."""


def max_body_from_env() -> int:
    raw = os.environ.get(MAX_BODY_ENV, "").strip()
    return int(raw) if raw else DEFAULT_MAX_BODY_BYTES


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, paths: frozenset[str], max_bytes: int) -> None:
        self.app = app
        self.paths = paths
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > self.max_bytes
                except ValueError:
                    too_big = False
                if too_big:
                    await self._reject(send)
                    return
        # Read the whole body (bounded), then replay it to the app.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break  # client disconnected
            body: bytes = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._reject(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps({"detail": f"request body larger than {self.max_bytes} bytes"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
