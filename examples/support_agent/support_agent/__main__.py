"""``python -m support_agent "Where's order 1042? I want a refund."``

Live: needs ``ANTHROPIC_API_KEY`` in the environment (read by the Anthropic client; never printed).
Offline: ``--mock-llm`` replays scripted Claude responses through the real, traced client.
Spans go to the LucentPad API at ``--endpoint`` (default ``LUCENTPAD_ENDPOINT`` or
``http://localhost:8000``).

Guardrail rules come from the API (the server's built-in ``refund_limit`` unless it is given a
rules file). ``--local-rules`` uses the same built-in rules without asking the API, for runs
where it isn't up. Budget alerts need the API's price table, so they need the API running.

Demo steps (with ``make dev`` running; add ``--mock-llm`` to run without an Anthropic key):
  3 redaction   "Where's order 1042? I want a refund."          email shows as [REDACTED:email]
  4 guardrail   "Order 1057 arrived broken, I want a refund."   refund_limit blocks the refund
  5 budget      "Where are orders 1042, 1043 and 1057?" --budget 0.001   budget alert fires
"""

from __future__ import annotations

import argparse
import sys

import anthropic

import lucentpad

from . import config
from .agent import build_client, run
from .rules import local_rules
from .tools import set_capture

__all__ = ["build_client", "main"]

DEFAULT_ENDPOINT = "http://localhost:8000"
DASHBOARD_URL = "http://localhost:5173"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="support_agent", description="LucentPad demo support agent")
    p.add_argument("question", nargs="?", default=config.DEFAULT_QUESTION)
    p.add_argument("--mock-llm", action="store_true", help="scripted model replies, no API key")
    p.add_argument(
        "--mock-delay",
        type=float,
        default=0.6,
        help="seconds per mocked model call, so the live waterfall draws step by step",
    )
    p.add_argument(
        "--endpoint", default=None, help="LucentPad API (default: http://localhost:8000)"
    )
    p.add_argument("--model", default=config.MODEL)
    p.add_argument("--no-content", action="store_true", help="don't capture prompt previews")
    p.add_argument(
        "--budget",
        type=float,
        default=config.BUDGET_USD_PER_RUN,
        metavar="USD",
        help=f"the run's budget (default {config.BUDGET_USD_PER_RUN:g}); a tiny one such as "
        "0.001 forces the budget alert",
    )
    p.add_argument(
        "--local-rules",
        action="store_true",
        help="use the built-in guardrail rules (refund_limit) instead of fetching the API's",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    lucentpad.init(
        args.endpoint or DEFAULT_ENDPOINT,  # the default lets LUCENTPAD_ENDPOINT override it
        service_name=config.SERVICE_NAME,
        capture_content=not args.no_content,
        rules=local_rules() if args.local_rules else None,
    )
    set_capture(not args.no_content)
    client = lucentpad.wrap(build_client(mock=args.mock_llm, mock_delay=args.mock_delay))
    try:
        result = run(client, args.question, model=args.model, budget_usd=args.budget)
    except anthropic.APIError as exc:
        lucentpad.flush(3.0)
        raise SystemExit(f"model call failed: {type(exc).__name__}") from None
    print(result.answer)
    if result.blocked_by:
        print(f"\n(blocked by guardrail {result.blocked_by})", file=sys.stderr)
    print(f"\ntrace {result.trace_id}: {DASHBOARD_URL}/traces/{result.trace_id}", file=sys.stderr)
    if not lucentpad.flush(5.0):
        print("warning: could not deliver every span to LucentPad", file=sys.stderr)
    lucentpad.shutdown()


if __name__ == "__main__":
    main()
