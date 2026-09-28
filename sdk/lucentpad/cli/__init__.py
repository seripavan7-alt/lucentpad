"""The ``lucentpad`` command line. One command so far::

    lucentpad eval evals/support_agent.yaml [--baseline evals/baseline.json] [--update-baseline]
        [--max-cost 0.10] [--endpoint URL] [--model NAME] [--no-post] [--mock]

See ``lucentpad.cli._eval`` (behaviour, exit codes) and ``lucentpad.cli._suite`` (suite format).
"""

from __future__ import annotations

import argparse

import httpx

from ._eval import add_arguments, run_eval

__all__ = ["main"]


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Entry point of the ``lucentpad`` script; returns the exit code. ``transport`` is a test
    seam for every HTTP call (the API and the SDK's span export)."""
    parser = argparse.ArgumentParser(prog="lucentpad", description="LucentPad command line")
    sub = parser.add_subparsers(dest="command", metavar="command")
    ev = sub.add_parser(
        "eval",
        help="run an eval suite and compare it with its baseline",
        description="Run an eval suite against its target, check each case, compare with the "
        "baseline and post the run to LucentPad. Exit 1 on a regression.",
    )
    add_arguments(ev)
    args = parser.parse_args(argv)
    if args.command != "eval":
        parser.print_help()
        return 2
    return run_eval(args, transport=transport)
