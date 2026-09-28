"""Guardrail rules (M3 step 3): built-in demo rules, the YAML rules file, reload on change, broken
files. No database."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import pytest

from lucentpad.guardrails import check_prompt, check_tool
from lucentpad_server.app import create_app
from lucentpad_server.guardrails import GuardrailProvider
from lucentpad_server.guardrails.rules import (
    BUILT_IN_SOURCE,
    FileGuardrails,
    RulesFileError,
    parse_document,
)

RULES_YAML = """
rules:
  - id: refund_limit
    type: tool
    tool: issue_refund
    condition: amount > 500
    message: Big refunds need a person.
  - id: no_secrets
    type: prompt
    keywords: [password]
    message: Don't paste credentials.
"""


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _write(path: Path, text: str, bump: int = 0) -> None:
    path.write_text(text)
    if bump:  # make sure the mtime differs even on coarse filesystems
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))


def test_built_in_rules() -> None:
    provider: GuardrailProvider = FileGuardrails(None)  # satisfies the contract (mypy)
    assert isinstance(provider, FileGuardrails)
    rules = provider.rules()
    assert rules.source == BUILT_IN_SOURCE
    (rule,) = rules.rules
    assert rule.id == "refund_limit" and rule.type == "tool"
    assert rule.tool == "issue_refund" and rule.condition == "amount > 200"
    assert rule.message == "Refunds over $200 need a human to approve them."
    assert len(rules.version) == 16
    engine = provider.engine_rules()
    assert check_tool(engine, "issue_refund", {"amount": 489}) is not None
    assert check_tool(engine, "issue_refund", {"amount": 77}) is None


def test_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LUCENTPAD_RULES_FILE", raising=False)
    assert FileGuardrails.from_env().rules().source == BUILT_IN_SOURCE
    path = tmp_path / "rules.yaml"
    _write(path, RULES_YAML)
    monkeypatch.setenv("LUCENTPAD_RULES_FILE", str(path))
    assert FileGuardrails.from_env().rules().source == str(path)


def test_file_rules_load(tmp_path: Path) -> None:
    path = tmp_path / "lucentpad.rules.yaml"
    _write(path, RULES_YAML)
    provider = FileGuardrails(path)
    rules = provider.rules()
    assert rules.source == str(path)
    assert [r.id for r in rules.rules] == ["refund_limit", "no_secrets"]
    assert rules.version != FileGuardrails(None).rules().version
    block = check_prompt(provider.engine_rules(), "my password is hunter2")
    assert block is not None and block.rule == "no_secrets"


def test_bare_list_and_empty_documents() -> None:
    assert [
        r.id
        for r in parse_document([{"id": "a", "type": "prompt", "keywords": ["x"], "message": "m"}])
    ] == ["a"]
    assert parse_document(None) == []
    assert parse_document({"rules": None}) == []


@pytest.mark.parametrize(
    ("doc", "needle"),
    [
        (
            {"rules": [{"id": "Bad Id", "type": "prompt", "keywords": ["x"], "message": "m"}]},
            "rule Bad Id",
        ),
        ({"rules": [{"id": "a", "type": "prompt", "message": "m"}]}, "keywords or a pattern"),
        ({"rules": [{"id": "a", "type": "prompt", "pattern": "(", "message": "m"}]}, "pattern"),
        (
            {
                "rules": [
                    {
                        "id": "a",
                        "type": "tool",
                        "tool": "t",
                        "condition": "amount >",
                        "message": "m",
                    }
                ]
            },
            "condition",
        ),
        (
            {
                "rules": [
                    {
                        "id": "a",
                        "type": "prompt",
                        "keywords": ["x"],
                        "message": "m",
                        "colour": "red",
                    }
                ]
            },
            "colour",
        ),
        (
            {"rules": [{"id": "a", "type": "prompt", "keywords": ["x"], "message": "m"}] * 2},
            "duplicate",
        ),
        ({"rules": {"id": "a"}}, "must be a list"),
        ({"rulez": []}, "unknown top-level"),
    ],
)
def test_invalid_documents(doc: object, needle: str) -> None:
    with pytest.raises(RulesFileError, match=needle):
        parse_document(doc)


def test_reload_on_change(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    _write(path, RULES_YAML)
    clock = Clock()
    provider = FileGuardrails(path, check_interval_s=1.0, clock=clock)
    v1 = provider.rules().version
    _write(path, RULES_YAML.replace("amount > 500", "amount > 900"), bump=5)
    # Within the check interval: not re-read yet.
    clock.t = 0.5
    assert provider.rules().version == v1
    clock.t = 1.6
    rules = provider.rules()
    assert rules.version != v1
    assert rules.rules[0].condition == "amount > 900"
    engine = provider.engine_rules()
    assert check_tool(engine, "issue_refund", {"amount": 600}) is None
    assert check_tool(engine, "issue_refund", {"amount": 901}) is not None
    # Unchanged file: same object, nothing re-parsed.
    clock.t = 5.0
    assert provider.rules() is rules


def test_invalid_file_keeps_previous_rules(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "rules.yaml"
    _write(path, RULES_YAML)
    provider = FileGuardrails(path, check_interval_s=0)
    good = provider.rules()
    for n, broken in enumerate(
        ["rules: [unclosed", "rules:\n  - id: x\n    type: prompt\n    message: no matcher\n"],
        start=1,
    ):
        _write(path, broken, bump=n)
        with caplog.at_level(logging.ERROR, logger="lucentpad_server.guardrails.rules"):
            assert provider.rules() == good
        assert provider.load_errors == n
        assert provider.last_error
        assert "keeping the previous 2 rule(s)" in caplog.text
    path.unlink()
    assert provider.rules() == good
    # Fixed again: picked up.
    _write(path, RULES_YAML.replace("password", "passphrase"), bump=9)
    assert provider.rules().rules[1].keywords == ["passphrase"]
    assert provider.last_error is None


def test_broken_file_at_startup_falls_back_to_built_in(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    _write(path, "rules: [unclosed")
    provider = FileGuardrails(path, check_interval_s=0)
    rules = provider.rules()
    assert [r.id for r in rules.rules] == ["refund_limit"] and rules.source == "built-in"
    assert provider.load_errors == 1 and provider.last_error
    missing = FileGuardrails(tmp_path / "nope.yaml", check_interval_s=0)
    assert missing.rules().source == "built-in" and missing.load_errors == 1
    # The file appears later: loaded on the next check.
    _write(tmp_path / "nope.yaml", RULES_YAML)
    assert len(missing.rules().rules) == 2


async def test_rules_route() -> None:
    app = create_app("postgresql://unused.invalid/none", seed_sample=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/guardrails/rules")).status_code == 501
        app.state.guardrails = FileGuardrails(None)
        r = await client.get("/v1/guardrails/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "built-in"
    assert body["rules"][0]["id"] == "refund_limit"
    assert body["version"] == FileGuardrails(None).rules().version
