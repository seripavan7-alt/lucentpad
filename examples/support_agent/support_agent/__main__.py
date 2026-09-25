"""``python -m support_agent "Where's order 1042? I want a refund."``

Live: needs ``ANTHROPIC_API_KEY`` in the environment (read by the Anthropic client; never printed).
Offline: ``--mock-llm`` replays scripted Claude responses through the real, traced client.
Spans go to the LucentPad API at ``--endpoint`` (default ``LUCENTPAD_ENDPOINT`` or
``http://localhost:8000``).
"""

from __future__ import annotations

import argparse
import os
import sys

import anthropic

import lucentpad

from . import config
from .agent import run
from .mock_llm import mock_client

DEFAULT_ENDPOINT = "http://localhost:8000"
DASHBOARD_URL = "http://localhost:5173"


def build_client(*, mock: bool, mock_delay: float) -> anthropic.Anthropic:
    if mock:
        return mock_client(mock_delay)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set. Set it, or run with --mock-llm.")
    return anthropic.Anthropic()  # the client reads the key from the environment itself


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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    lucentpad.init(
        args.endpoint or DEFAULT_ENDPOINT,  # the default lets LUCENTPAD_ENDPOINT override it
        service_name=config.SERVICE_NAME,
        capture_content=not args.no_content,
    )
    client = lucentpad.wrap(build_client(mock=args.mock_llm, mock_delay=args.mock_delay))
    try:
        result = run(client, args.question, model=args.model)
    except anthropic.APIError as exc:
        lucentpad.flush(3.0)
        raise SystemExit(f"model call failed: {type(exc).__name__}") from None
    print(result.answer)
    print(f"\ntrace {result.trace_id}: {DASHBOARD_URL}/traces/{result.trace_id}", file=sys.stderr)
    if not lucentpad.flush(5.0):
        print("warning: could not deliver every span to LucentPad", file=sys.stderr)
    lucentpad.shutdown()


if __name__ == "__main__":
    main()
