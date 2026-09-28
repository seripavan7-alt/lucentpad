"""Prompt/response previews (D6/D9): at most ``PREVIEW_MAX_CHARS`` chars plus a truncated flag.

Previews are redacted before export (D20). To catch a secret that straddles the cut, a preview
is captured with ``REDACT_MARGIN`` extra characters, redacted, and only then cut to the limit
(never inside a ``[REDACTED:...]`` marker).
"""

from __future__ import annotations

from ._attrs import PREVIEW_MAX_CHARS

REDACT_MARGIN = 512
CAPTURE_CHARS = PREVIEW_MAX_CHARS + REDACT_MARGIN
_MARKER = "[REDACTED:"


def truncate(text: str, limit: int = PREVIEW_MAX_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def cut(text: str, limit: int = PREVIEW_MAX_CHARS) -> tuple[str, bool]:
    """Cut an already-redacted preview to ``limit`` without splitting a redaction marker."""
    if len(text) <= limit:
        return text, False
    # A marker that begins before the cut and ends after it (``rfind`` needs the whole marker
    # inside its window, so widen it by the marker's length).
    start = text.rfind(_MARKER, 0, limit + len(_MARKER) - 1)
    if 0 <= start < limit and text.find("]", start) >= limit:
        return text[:start], True
    return text[:limit], True


class PreviewBuilder:
    """Collects streamed text without holding more than the cap."""

    __slots__ = ("_limit", "_parts", "_size", "truncated")

    def __init__(self, limit: int = CAPTURE_CHARS) -> None:
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
        """Start a new content block: separated from what came before by a newline."""
        if self._size:
            self.add("\n")
        self.add(text)

    @property
    def empty(self) -> bool:
        return self._size == 0 and not self.truncated

    def text(self) -> str:
        return "".join(self._parts)
