"""Prompt/response previews (D6/D9): at most ``PREVIEW_MAX_CHARS`` chars plus a truncated flag."""

from __future__ import annotations

from ._attrs import PREVIEW_MAX_CHARS


def truncate(text: str, limit: int = PREVIEW_MAX_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


class PreviewBuilder:
    """Collects streamed text without holding more than the cap."""

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
        """Start a new content block: separated from what came before by a newline."""
        if self._size:
            self.add("\n")
        self.add(text)

    @property
    def empty(self) -> bool:
        return self._size == 0 and not self.truncated

    def text(self) -> str:
        return "".join(self._parts)
