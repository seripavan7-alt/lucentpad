"""Who is calling: client detection from request headers, and the key fingerprint.

Matched (lowercase substring of ``User-Agent`` unless noted), first rule wins; extra rules from
``LUCENTPAD_GATEWAY_CLIENT_UA`` are checked before these:

- ``claude-code``: ``claude-cli/`` (Claude Code's UA is ``claude-cli/<version> (external, cli)``),
  ``claude-code``, or an ``x-claude-code-session-id`` header (documented at
  code.claude.com/docs/en/llm-gateway-protocol).
- ``copilot-cli``: UA starting ``copilot/`` (Copilot CLI's UA is
  ``copilot/<version> (<platform> <node>) OpenAI/<sdk>``, seen in public copilot-cli debug logs),
  ``copilot-cli``, ``githubcopilotcli``, or ``copilot-integration-id: copilot-developer-cli``.
- ``copilot-chat``: ``githubcopilotchat`` (the Copilot Chat extension's UA
  ``GitHubCopilotChat/<version>``), ``copilot-chat``, ``copilot-integration-id: vscode-chat``,
  or an ``editor-version: vscode/...`` header.
- anything else: ``other``.

The BYOK UAs are not documented anywhere official (checked 2026-09-25); verify at the M2
checkpoint and add a rule here or via the env var if a client shows up as ``other``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from starlette.datastructures import Headers

ClientRule = tuple[str, Callable[[str, Headers], bool]]


def _ua_has(*needles: str) -> Callable[[str, Headers], bool]:
    return lambda ua, _h: any(n in ua for n in needles)


BUILTIN_RULES: list[ClientRule] = [
    ("claude-code", _ua_has("claude-cli/", "claude-code")),
    ("claude-code", lambda _ua, h: "x-claude-code-session-id" in h),
    ("copilot-cli", lambda ua, _h: ua.startswith("copilot/")),
    ("copilot-cli", _ua_has("copilot-cli", "githubcopilotcli")),
    (
        "copilot-cli",
        lambda _ua, h: h.get("copilot-integration-id", "").lower() == "copilot-developer-cli",
    ),
    ("copilot-chat", _ua_has("githubcopilotchat", "copilot-chat")),
    ("copilot-chat", lambda _ua, h: h.get("copilot-integration-id", "").lower() == "vscode-chat"),
    ("copilot-chat", lambda _ua, h: h.get("editor-version", "").lower().startswith("vscode/")),
]


def detect_client(headers: Headers, extra: tuple[tuple[str, str], ...] = ()) -> str:
    ua = headers.get("user-agent", "").lower()
    for client, needle in extra:
        if needle in ua:
            return client
    for client, match in BUILTIN_RULES:
        if match(ua, headers):
            return client
    return "other"


def credential(headers: Headers) -> str | None:
    """The client's API credential (``x-api-key``, else the ``Authorization: Bearer`` token)."""
    key = headers.get("x-api-key", "").strip()
    if key:
        return key
    auth = headers.get("authorization", "").strip()
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return auth or None


def key_fingerprint(headers: Headers, salt: bytes) -> str | None:
    """Salted SHA-256 of the credential, first 12 hex chars. The key itself is never kept."""
    cred = credential(headers)
    if cred is None:
        return None
    return hashlib.sha256(salt + b"\0" + cred.encode()).hexdigest()[:12]
