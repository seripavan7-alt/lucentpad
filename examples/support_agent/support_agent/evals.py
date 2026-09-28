"""The ``lucentpad eval`` target for the demo agent (``evals/support_agent.yaml``).

``lucentpad eval`` initialises the SDK (endpoint, rules, span capture) and wraps each case in its
own trace, so this only builds the client and runs the agent.
"""

from __future__ import annotations

import lucentpad

from . import config
from .agent import build_client, run


def run_case(input: str, *, model: str | None = None, mock: bool = False) -> dict[str, str]:
    """Answer one eval case. ``mock``: scripted replies (no key); else the real Anthropic API."""
    client = lucentpad.wrap(build_client(mock=mock))
    result = run(client, input, model=model or config.MODEL)
    return {"answer": result.answer, "trace_id": result.trace_id}
