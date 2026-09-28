"""The active blocking rules: ``LUCENTPAD_RULES_FILE`` (YAML) or the built-in demo rules.

``FileGuardrails`` is the app's ``GuardrailProvider``. It re-reads the file whenever its mtime
(or size) changes, checked at most once per ``check_interval_s``. A file that fails to load
(unreadable, bad YAML, a rule that fails ``schema.GuardrailRule`` or the engine's
``parse_rules``) is logged and ignored: the previous rules stay active and the API keeps
serving. When the configured file is broken at startup there are no previous rules, so no rule
blocks until it is fixed (the error names the problem).

File format (``{"rules": [...]}`` or a bare list; fields as ``schema.GuardrailRule``)::

    rules:
      - id: refund_limit
        type: tool
        tool: issue_refund
        condition: amount > 200
        message: Refunds over $200 need a human to approve them.
      - id: no_secrets
        type: prompt
        keywords: [password, "api key"]
        message: Don't paste credentials into the chat.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from lucentpad.guardrails import Rule, parse_rules
from lucentpad_server.schema import GuardrailRule, GuardrailRules

log = logging.getLogger(__name__)

RULES_FILE_ENV = "LUCENTPAD_RULES_FILE"
BUILT_IN_SOURCE = "built-in"

BUILT_IN_RULES: tuple[GuardrailRule, ...] = (
    GuardrailRule(
        id="refund_limit",
        type="tool",
        description="Demo support agent: refunds above the automatic limit need a person.",
        tool="issue_refund",
        condition="amount > 200",
        message="Refunds over $200 need a human to approve them.",
    ),
)


class RulesFileError(ValueError):
    """The rules document is invalid (the message says which rule and why)."""


def rules_version(rules: Sequence[GuardrailRule]) -> str:
    """A short hash of the rules' content: changes whenever any rule changes."""
    canonical = json.dumps([r.model_dump(mode="json") for r in rules], sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _engine_input(rules: Sequence[GuardrailRule]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json", exclude_defaults=True) for r in rules]


def to_engine(rules: Sequence[GuardrailRule]) -> list[Rule]:
    """The engine's rules for already-validated API rules (``lucentpad.guardrails.Rule``)."""
    return parse_rules(_engine_input(rules))


def parse_document(data: Any) -> list[GuardrailRule]:
    """Validate a parsed rules document (``{"rules": [...]}`` or a bare list) with
    ``schema.GuardrailRule`` and the engine's ``parse_rules``. Raises ``RulesFileError``."""
    if data is None:
        items: Any = []
    elif isinstance(data, Mapping):
        extra = set(data) - {"rules"}
        if extra:
            raise RulesFileError(f"unknown top-level keys: {', '.join(sorted(map(str, extra)))}")
        items = data.get("rules") or []
    else:
        items = data
    if not isinstance(items, list):
        raise RulesFileError("`rules` must be a list")
    rules: list[GuardrailRule] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        label = item.get("id", f"#{i + 1}") if isinstance(item, Mapping) else f"#{i + 1}"
        try:
            rule = GuardrailRule.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "rule"
            raise RulesFileError(f"rule {label}: {where}: {first['msg']}") from None
        if rule.id in seen:
            raise RulesFileError(f"rule {rule.id}: duplicate id")
        seen.add(rule.id)
        rules.append(rule)
    try:
        parse_rules(_engine_input(rules))
    except ValueError as exc:
        raise RulesFileError(str(exc)) from None
    return rules


def load_file(path: Path) -> list[GuardrailRule]:
    """Read, parse and validate a rules file. Raises ``OSError`` or ``RulesFileError``."""
    text = path.read_text(encoding="utf-8")
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesFileError(f"invalid YAML: {exc}") from None
    return parse_document(data)


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class FileGuardrails:
    """``GuardrailProvider`` over a rules file (``path``) or the built-in rules (``path=None``)."""

    def __init__(
        self,
        path: Path | None,
        *,
        check_interval_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self._interval = check_interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at = float("-inf")
        self._stamp: tuple[int, int] | None = None
        self.load_errors = 0
        """Loads that failed (the previous rules stayed active)."""
        self.last_error: str | None = None
        # Start from the built-in rules: if the file can't be loaded at startup, they stay
        # active (and the error is logged) rather than leaving nothing to block.
        self._set(list(BUILT_IN_RULES), BUILT_IN_SOURCE)
        if path is not None:
            self._check(force=True)

    @classmethod
    def from_env(cls) -> FileGuardrails:
        """``$LUCENTPAD_RULES_FILE``, else the built-in demo rules."""
        raw = os.environ.get(RULES_FILE_ENV, "").strip()
        return cls(Path(raw).expanduser() if raw else None)

    def _set(self, rules: list[GuardrailRule], source: str) -> None:
        self._current = GuardrailRules(rules=rules, source=source, version=rules_version(rules))
        self._engine: list[Rule] | None = None  # built lazily, once per version

    # ------------------------------------------------------------------ GuardrailProvider

    def rules(self) -> GuardrailRules:
        """The active rules, reloaded first if the file changed since the last check."""
        self._check()
        return self._current

    def engine_rules(self) -> list[Rule]:
        """The active rules for the engine (``check_prompt`` / ``check_tool``)."""
        current = self.rules()
        with self._lock:
            if self._engine is None or self._current is not current:
                self._engine = to_engine(current.rules)
            return self._engine

    # ------------------------------------------------------------------ reloading

    def _check(self, *, force: bool = False) -> None:
        if self.path is None:
            return
        now = self._clock()
        if not force and now - self._checked_at < self._interval:
            return
        with self._lock:
            self._checked_at = now
            stamp = _stamp(self.path)
            if not force and stamp == self._stamp:
                return
            self._stamp = stamp
            try:
                if stamp is None:
                    raise OSError(f"cannot read {self.path}")
                rules = load_file(self.path)
            except (OSError, RulesFileError, UnicodeDecodeError) as exc:
                self.load_errors += 1
                self.last_error = str(exc)
                log.error(
                    "guardrails: could not load %s (%s); keeping the previous %d rule(s) from %s",
                    self.path,
                    exc,
                    len(self._current.rules),
                    self._current.source,
                )
                return
            self.last_error = None
            self._set(rules, str(self.path))
            log.info(
                "guardrails: loaded %d rule(s) from %s (version %s)",
                len(rules),
                self.path,
                self._current.version,
            )
