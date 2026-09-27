"""Gateway building blocks: client detection, fingerprints, sessions, SSE parsing, config."""

from __future__ import annotations

import gzip

import pytest
from starlette.datastructures import Headers

from lucentpad_server.gateway.clients import detect_client, key_fingerprint
from lucentpad_server.gateway.config import GatewayConfig
from lucentpad_server.gateway.parse import (
    StreamParser,
    anthropic_input_preview,
    openai_input_preview,
    parse_json_response,
)
from lucentpad_server.gateway.sessions import SessionTracker
from lucentpad_server.schema import PREVIEW_MAX_CHARS

from .gateway_fakes import ANTHROPIC_STREAM, chop, openai_stream


@pytest.mark.parametrize(
    ("headers", "client"),
    [
        ({"user-agent": "claude-cli/2.1.300 (external, cli)"}, "claude-code"),
        ({"user-agent": "claude-cli/2.0.1 (external, claude-vscode)"}, "claude-code"),
        ({"user-agent": "Anthropic/JS 0.60", "x-claude-code-session-id": "s"}, "claude-code"),
        ({"user-agent": "copilot/1.0.12 (darwin v24.3.0) OpenAI/5.20.1"}, "copilot-cli"),
        (
            {"user-agent": "Anthropic/JS 0.60", "copilot-integration-id": "copilot-developer-cli"},
            "copilot-cli",
        ),
        ({"user-agent": "GitHubCopilotChat/0.40.0"}, "copilot-chat"),
        ({"user-agent": "node", "editor-version": "vscode/1.122.0"}, "copilot-chat"),
        ({"user-agent": "OpenAI/JS 5.0", "copilot-integration-id": "vscode-chat"}, "copilot-chat"),
        ({"user-agent": "curl/8.7.1"}, "other"),
        ({}, "other"),
    ],
)
def test_detect_client(headers: dict[str, str], client: str) -> None:
    assert detect_client(Headers(headers)) == client


def test_extra_client_rules_win() -> None:
    headers = Headers({"user-agent": "MyAgent/1.0 claude-cli/2"})
    assert detect_client(headers, (("my-agent", "myagent/"),)) == "my-agent"


def test_key_fingerprint() -> None:
    a = key_fingerprint(Headers({"x-api-key": "sk-1"}), b"salt")
    assert a is not None and len(a) == 12 and "sk-1" not in a
    assert a == key_fingerprint(Headers({"authorization": "Bearer sk-1"}), b"salt")
    assert a != key_fingerprint(Headers({"x-api-key": "sk-1"}), b"other-salt")
    assert a != key_fingerprint(Headers({"x-api-key": "sk-2"}), b"salt")
    assert key_fingerprint(Headers({}), b"salt") is None


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUCENTPAD_ANTHROPIC_UPSTREAM", "http://fake:1")
    monkeypatch.setenv("LUCENTPAD_KEY_SALT", "pepper")
    monkeypatch.setenv("LUCENTPAD_GATEWAY_SESSION_HEADERS", "X-Session, x-other")
    monkeypatch.setenv("LUCENTPAD_GATEWAY_CLIENT_UA", "cursor=cursor/,bad")
    cfg = GatewayConfig.from_env()
    assert cfg.anthropic_upstream == "http://fake:1"
    assert cfg.openai_upstream == "https://api.openai.com"
    assert cfg.key_salt == b"pepper"
    assert cfg.session_headers == ("x-session", "x-other")
    assert cfg.extra_client_rules == (("cursor", "cursor/"),)
    assert cfg.failover is True
    monkeypatch.delenv("LUCENTPAD_KEY_SALT")
    assert GatewayConfig.from_env().key_salt != GatewayConfig.from_env().key_salt  # random


def test_sessions_expire_and_prune() -> None:
    now = [0.0]
    tracker = SessionTracker(gap_s=100, clock=lambda: now[0])
    s1, new1 = tracker.lookup("claude-code", None, "fp")
    s2, new2 = tracker.lookup("claude-code", None, "fp")
    assert new1 and not new2 and s1 is s2
    now[0] = 99
    assert tracker.lookup("claude-code", None, "fp") == (s1, False)
    s3, _ = tracker.lookup("copilot-cli", None, "fp")  # other client, same key: other session
    assert s3.trace_id != s1.trace_id
    now[0] = 500
    s4, new4 = tracker.lookup("claude-code", None, "fp")
    assert new4 and s4.trace_id != s1.trace_id
    assert len(tracker) == 1  # the idle ones were pruned


def test_stream_parser_handles_any_chunking() -> None:
    for sizes in ((1,), (2, 3), (7, 50, 3, 120, 1, 64), (len(ANTHROPIC_STREAM),)):
        p = StreamParser("anthropic")
        for c in chop(ANTHROPIC_STREAM, sizes):
            p.feed(c)
        p.close()
        assert p.ok
        assert (p.info.input_tokens, p.info.output_tokens) == (1200, 42)
        assert p.info.output.text() == "Let me read the file — ünïcode first.\n[tool_use Read]"


def test_stream_parser_crlf_and_gzip() -> None:
    raw = openai_stream(True).replace(b"\n", b"\r\n")
    p = StreamParser("openai")
    for c in chop(raw, (11,)):
        p.feed(c)
    p.close()
    assert (p.info.input_tokens, p.info.output_tokens) == (50, 12)

    z = StreamParser("openai", "gzip")
    for c in chop(gzip.compress(openai_stream(True)), (9,)):
        z.feed(c)
    z.close()
    assert z.ok and z.info.output_tokens == 12

    br = StreamParser("openai", "br")
    br.feed(b"\x00\x01")
    assert not br.ok and br.info.output_tokens is None


def test_stream_parser_survives_garbage() -> None:
    p = StreamParser("anthropic")
    p.feed(b"data: {not json\n\n\xff\xfe\n\ndata: [1,2]\n\n")
    p.close()
    assert p.ok and p.info.input_tokens is None


def test_output_preview_is_capped() -> None:
    long_text = "x" * (PREVIEW_MAX_CHARS + 50)
    info = parse_json_response(
        "anthropic",
        ('{"type":"message","content":[{"type":"text","text":"' + long_text + '"}]}').encode(),
    )
    assert len(info.output.text()) == PREVIEW_MAX_CHARS and info.output.truncated


def test_input_previews() -> None:
    assert anthropic_input_preview([{"role": "user", "content": "hi"}]) == "hi"
    assert anthropic_input_preview("nope") is None
    msgs = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert openai_input_preview(msgs) == "[tool_result bash]"
    parts = [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "image_url"}]}]
    assert openai_input_preview(parts) == "a\n[image_url]"


def test_cache_usage_counts_as_input_and_is_recorded_apart() -> None:
    import json as _json

    from lucentpad_server.gateway.parse import parse_json_response as _parse

    anthropic = _parse(
        "anthropic",
        _json.dumps(
            {
                "type": "message",
                "model": "claude-sonnet-5",
                "content": [],
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 9000,
                    "cache_creation_input_tokens": 900,
                    "output_tokens": 40,
                },
            }
        ).encode(),
    )
    assert (anthropic.input_tokens, anthropic.cache_read_tokens) == (10_000, 9000)
    assert anthropic.cache_creation_tokens == 900
    openai = _parse(
        "openai",
        _json.dumps(
            {
                "model": "gpt-5-2026-08-07",
                "choices": [],
                "usage": {
                    "prompt_tokens": 5000,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 4096},
                },
            }
        ).encode(),
    )
    assert (openai.input_tokens, openai.cache_read_tokens) == (5000, 4096)
    assert openai.cache_creation_tokens is None
