"""Session grouping (D16): one trace per client session.

- A client session header (``GatewayConfig.session_headers``, default Claude Code's
  ``x-claude-code-session-id``) wins: the trace and root span ids are derived from
  ``(client, header value)``, so the session keeps its trace forever, across idle gaps and
  gateway restarts (a re-sent root span is ignored by the store: same primary key).
- Otherwise the key is ``(client, key fingerprint)``: requests within ``gap_s`` of the previous
  one join its trace; after a longer gap (or a new key) a new trace starts.

The map lives in memory and is pruned of entries idle for longer than the gap.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Session:
    trace_id: str
    root_span_id: str
    session_id: str
    client: str
    last_seen: float
    spent_usd: float = 0.0
    """Estimated spend of the session's turns so far (budget alerts, D23)."""
    budget_alerted: bool = False


def new_hex_id(n_bytes: int) -> str:
    while True:
        value = secrets.token_hex(n_bytes)
        if value.strip("0"):
            return value


class SessionTracker:
    def __init__(self, gap_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._gap = gap_s
        self._clock = clock
        self._sessions: dict[tuple[str, str, str], Session] = {}
        self._last_prune = clock()

    def __len__(self) -> int:
        return len(self._sessions)

    def lookup(
        self, client: str, header_session: str | None, fingerprint: str | None
    ) -> tuple[Session, bool]:
        """The session for this request and whether it is new (its root span must be sent)."""
        now = self._clock()
        self._maybe_prune(now)
        if header_session:
            key = ("h", client, header_session)
            session = self._sessions.get(key)
            if session is not None:
                session.last_seen = now
                return session, False
            digest = hashlib.sha256(f"lucentpad-session\0{client}\0{header_session}".encode())
            hexd = digest.hexdigest()
            session = Session(
                trace_id=hexd[:32] if hexd[:32].strip("0") else new_hex_id(16),
                root_span_id=hexd[32:48] if hexd[32:48].strip("0") else new_hex_id(8),
                session_id=header_session[:200],
                client=client,
                last_seen=now,
            )
            self._sessions[key] = session
            return session, True
        key = ("k", client, fingerprint or "")
        session = self._sessions.get(key)
        if session is not None and now - session.last_seen <= self._gap:
            session.last_seen = now
            return session, False
        session = Session(
            trace_id=new_hex_id(16),
            root_span_id=new_hex_id(8),
            session_id=f"{client}-{secrets.token_hex(6)}",
            client=client,
            last_seen=now,
        )
        self._sessions[key] = session
        return session, True

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < min(60.0, self._gap):
            return
        self._last_prune = now
        stale = [k for k, s in self._sessions.items() if now - s.last_seen > self._gap]
        for k in stale:
            del self._sessions[k]
