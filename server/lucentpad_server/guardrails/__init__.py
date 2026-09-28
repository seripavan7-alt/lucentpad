"""Guardrails on the server: the active blocking rules (from ``LUCENTPAD_RULES_FILE`` or the
built-in demo rules), redaction at ingest, and enforcement in the gateway.

``GuardrailProvider`` is the seam the routes depend on (part of the API contract). The app's
lifespan puts an implementation on ``app.state.guardrails``; without one the rules route is 501.
The matching and redaction logic itself lives in ``lucentpad.guardrails`` (the SDK package), so the
SDK, the gateway and ingest all apply exactly the same rules.
"""

from __future__ import annotations

from typing import Protocol

from lucentpad_server.schema import GuardrailRules


class GuardrailProvider(Protocol):
    def rules(self) -> GuardrailRules:
        """The active rules (reloaded when the file changes)."""
        ...
