"""Gateway settings, read from the environment once at startup.

- ``LUCENTPAD_ANTHROPIC_UPSTREAM``: Anthropic base URL (``https://api.anthropic.com``).
- ``LUCENTPAD_OPENAI_UPSTREAM``: OpenAI base URL (``https://api.openai.com``).
- ``LUCENTPAD_GATEWAY_FAILOVER``: ``0`` turns off retry + fallback model (D18); default on.
- ``LUCENTPAD_KEY_SALT``: salt of the key fingerprint; default random per process (see below).
- ``LUCENTPAD_GATEWAY_SESSION_GAP``: idle seconds that end a key-based session (1800).
- ``LUCENTPAD_GATEWAY_SESSION_HEADERS``: client session headers, comma separated, first match
  wins (``x-claude-code-session-id``).
- ``LUCENTPAD_GATEWAY_READ_TIMEOUT``: seconds between upstream bytes before a call fails (600).
- ``LUCENTPAD_GATEWAY_CAPTURE_CONTENT``: ``0`` stops recording prompt/response previews.
- ``LUCENTPAD_GATEWAY_CLIENT_UA``: extra User-Agent rules ``client=substring,...`` (``clients``).

Without ``LUCENTPAD_KEY_SALT`` the salt is random per process, so key fingerprints change on
restart: key-based sessions (no client session header) don't continue across a restart, and
the same key shows a different fingerprint before and after it.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field

DEFAULT_ANTHROPIC_UPSTREAM = "https://api.anthropic.com"
DEFAULT_OPENAI_UPSTREAM = "https://api.openai.com"
DEFAULT_SESSION_HEADERS = ("x-claude-code-session-id",)
DEFAULT_SESSION_GAP_S = 30 * 60.0

FALLBACK_MODELS: dict[str, str] = {
    "claude-sonnet-5": "claude-haiku-4-5",
    "claude-opus-5-5": "claude-sonnet-5",
    "gpt-5": "gpt-5-mini",
}
"""D18 failover pairs: primary model → fallback (same provider)."""


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _client_rules(raw: str) -> tuple[tuple[str, str], ...]:
    rules: list[tuple[str, str]] = []
    for item in raw.split(","):
        client, sep, needle = item.partition("=")
        if sep and client.strip() and needle.strip():
            rules.append((client.strip(), needle.strip().lower()))
    return tuple(rules)


@dataclass(frozen=True)
class GatewayConfig:
    anthropic_upstream: str = DEFAULT_ANTHROPIC_UPSTREAM
    openai_upstream: str = DEFAULT_OPENAI_UPSTREAM
    failover: bool = True
    failover_backoff_s: float = 0.25
    """Wait before the same-model retry and before the fallback-model attempt."""
    key_salt: bytes = field(default_factory=lambda: secrets.token_bytes(16), repr=False)
    session_gap_s: float = DEFAULT_SESSION_GAP_S
    session_headers: tuple[str, ...] = DEFAULT_SESSION_HEADERS
    read_timeout_s: float = 600.0
    connect_timeout_s: float = 10.0
    capture_content: bool = True
    extra_client_rules: tuple[tuple[str, str], ...] = ()
    """``(client, lowercase User-Agent substring)`` checked before the built-in rules."""
    fallback_models: dict[str, str] = field(default_factory=lambda: dict(FALLBACK_MODELS))

    def upstream(self, provider: str) -> str:
        return self.anthropic_upstream if provider == "anthropic" else self.openai_upstream

    @classmethod
    def from_env(cls) -> GatewayConfig:
        salt = os.environ.get("LUCENTPAD_KEY_SALT", "")
        headers = os.environ.get("LUCENTPAD_GATEWAY_SESSION_HEADERS", "").strip()
        return cls(
            anthropic_upstream=os.environ.get("LUCENTPAD_ANTHROPIC_UPSTREAM", "").strip()
            or DEFAULT_ANTHROPIC_UPSTREAM,
            openai_upstream=os.environ.get("LUCENTPAD_OPENAI_UPSTREAM", "").strip()
            or DEFAULT_OPENAI_UPSTREAM,
            failover=_flag("LUCENTPAD_GATEWAY_FAILOVER", True),
            key_salt=salt.encode() if salt else secrets.token_bytes(16),
            session_gap_s=_float("LUCENTPAD_GATEWAY_SESSION_GAP", DEFAULT_SESSION_GAP_S),
            session_headers=tuple(h.strip().lower() for h in headers.split(",") if h.strip())
            if headers
            else DEFAULT_SESSION_HEADERS,
            read_timeout_s=_float("LUCENTPAD_GATEWAY_READ_TIMEOUT", 600.0),
            capture_content=_flag("LUCENTPAD_GATEWAY_CAPTURE_CONTENT", True),
            extra_client_rules=_client_rules(os.environ.get("LUCENTPAD_GATEWAY_CLIENT_UA", "")),
        )
