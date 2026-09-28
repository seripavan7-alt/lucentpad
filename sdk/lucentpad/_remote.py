"""Guardrail rules and the price table, fetched from the LucentPad API in the background.

A daemon thread fetches ``GET /v1/guardrails/rules`` and ``GET /v1/pricing`` right after
``init`` and then every 60 s (every 5 s while the API can't be reached). The host never waits
for it, with one bounded exception: the first guardrail check of the process waits up to
``FIRST_FETCH_WAIT`` for the first fetch to finish, so a run that starts right after ``init``
is not let through just because the rules were still in flight.

Failure behaviour (D21/D23):
- API answers 404/501 for rules (no rules configured on this server): no rules.
- API unreachable, 5xx, or a malformed answer: keep the last known rules and prices (none yet:
  no rules, budgets skipped). Redaction never depends on the API.
- Rules given locally (``init(rules=...)``) replace the API's rules; they are never fetched.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

import httpx

from ._pricing import Price, parse_price_table
from .guardrails import Rule, parse_rules

log = logging.getLogger("lucentpad")

REFRESH_INTERVAL = 60.0
RETRY_INTERVAL = 5.0
FIRST_FETCH_WAIT = 0.5


class RuleSet(NamedTuple):
    prompt: tuple[Rule, ...]
    tool: Mapping[str, tuple[Rule, ...]]

    @property
    def empty(self) -> bool:
        return not self.prompt and not self.tool


EMPTY_RULES = RuleSet((), {})


def build_ruleset(rules: Sequence[Rule]) -> RuleSet:
    by_tool: dict[str, list[Rule]] = {}
    for r in rules:
        if r.type == "tool" and r.tool:
            by_tool.setdefault(r.tool, []).append(r)
    return RuleSet(
        tuple(r for r in rules if r.type == "prompt"),
        {k: tuple(v) for k, v in by_tool.items()},
    )


def local_ruleset(rules: Any) -> RuleSet:
    """``init(rules=...)``: a parsed rules document, a list of rule dicts, or ``Rule`` objects.
    Raises ``ValueError`` on an invalid document."""
    if isinstance(rules, Sequence) and all(isinstance(r, Rule) for r in rules):
        return build_ruleset(list(rules))
    if isinstance(rules, Mapping | Sequence) and not isinstance(rules, str | bytes):
        return build_ruleset(parse_rules(rules))
    raise ValueError("rules must be a rules document, a list of rules or a list of Rule")


class Remote:
    def __init__(
        self,
        endpoint: str,
        client: httpx.Client,
        *,
        local_rules: RuleSet | None = None,
        interval: float = REFRESH_INTERVAL,
        retry_interval: float = RETRY_INTERVAL,
        start: bool = True,
    ) -> None:
        base = endpoint.rstrip("/")
        self._rules_url = base + "/v1/guardrails/rules"
        self._prices_url = base + "/v1/pricing"
        self._client = client
        self._local = local_rules is not None
        self._rules: RuleSet = local_rules if local_rules is not None else EMPTY_RULES
        self._rules_version: str | None = None
        self._prices: dict[str, Price] | None = None
        self._interval = interval
        self._retry = min(retry_interval, interval)
        self._stop = threading.Event()
        self._first = threading.Event()
        if self._local:
            self._first.set()  # nothing to wait for
        self._waited = False
        self._warned: set[str] = set()
        self.fetches = 0  # completed fetch rounds (tests)
        self._thread = threading.Thread(target=self._run, name="lucentpad-config", daemon=True)
        if start:
            self._thread.start()

    # ------------------------------------------------------------------ host side

    def rules(self, *, wait: bool = True) -> RuleSet:
        if wait and not self._first.is_set() and not self._waited:
            self._waited = True
            self._first.wait(FIRST_FETCH_WAIT)
        return self._rules

    async def arules(self) -> RuleSet:
        if not self._first.is_set() and not self._waited:
            self._waited = True
            await asyncio.to_thread(self._first.wait, FIRST_FETCH_WAIT)
        return self._rules

    def prices(self) -> Mapping[str, Price] | None:
        return self._prices

    def wait_first(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(0.5)

    # ------------------------------------------------------------------ fetch thread

    def _run(self) -> None:
        while not self._stop.is_set():
            ok = True
            try:
                ok = self.fetch_once()
            except Exception:  # never let the thread die
                log.debug("lucentpad: config fetch error", exc_info=True)
                ok = False
            finally:
                self._first.set()
            self._stop.wait(self._interval if ok else self._retry)

    def fetch_once(self) -> bool:
        """One round; True when both answers were definitive (success, or 'not configured')."""
        ok_rules = True if self._local else self._fetch_rules()
        self._first.set()  # rules settled: don't make the first check wait for prices too
        ok_prices = self._fetch_prices()
        self.fetches += 1
        return ok_rules and ok_prices

    def _get(self, url: str) -> httpx.Response | None:
        try:
            return self._client.get(url, headers={"accept": "application/json"})
        except Exception:
            log.debug("lucentpad: could not reach %s", url)
            return None

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg)

    def _fetch_rules(self) -> bool:
        resp = self._get(self._rules_url)
        if resp is None:
            return False
        if resp.status_code in (404, 501):
            self._rules, self._rules_version = EMPTY_RULES, None
            return True
        if resp.status_code != 200:
            return False
        try:
            body = resp.json()
            version = body.get("version") if isinstance(body, dict) else None
            if isinstance(version, str) and version == self._rules_version:
                return True
            rules = build_ruleset(parse_rules(body))
        except Exception as exc:
            self._warn_once("rules", f"lucentpad: ignoring invalid guardrail rules: {exc}")
            return False
        self._rules = rules
        self._rules_version = version if isinstance(version, str) else None
        return True

    def _fetch_prices(self) -> bool:
        resp = self._get(self._prices_url)
        if resp is None:
            return False
        if resp.status_code in (404, 501):
            return True  # keep the last known table
        if resp.status_code != 200:
            return False
        try:
            self._prices = parse_price_table(resp.json())
        except Exception as exc:
            self._warn_once("prices", f"lucentpad: ignoring invalid price table: {exc}")
            return False
        return True
