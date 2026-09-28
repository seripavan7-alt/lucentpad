"""The tool-use loop over a LucentPad-wrapped Anthropic client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types import MessageParam, ToolResultBlockParam

import lucentpad

from . import config
from .mock_llm import mock_client
from .tools import TOOLS, run_tool


@dataclass(frozen=True)
class RunResult:
    answer: str
    trace_id: str
    turns: int
    blocked_by: str | None = None  # the guardrail rule that stopped the run, if any


def build_client(*, mock: bool, mock_delay: float = 0.0) -> anthropic.Anthropic:
    """The (unwrapped) Anthropic client: scripted replies with ``mock``, else the real API."""
    if mock:
        return mock_client(mock_delay)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set. Set it, or run with --mock-llm.")
    return anthropic.Anthropic()  # the client reads the key from the environment itself


def handoff_message(blocked: lucentpad.GuardrailBlocked) -> str:
    return config.HANDOFF_REFUND if blocked.rule == "refund_limit" else config.HANDOFF_OTHER


def run(
    client: anthropic.Anthropic,
    question: str,
    *,
    model: str = config.MODEL,
    budget_usd: float | None = config.BUDGET_USD_PER_RUN,
) -> RunResult:
    """Answer one customer message. The run is one trace: agent root, llm and tool spans.

    ``budget_usd`` is the run's spend limit (alert mode: the model call that crosses it gets a
    ``lucentpad.budget.alert`` event). A guardrail block (a refund over the limit, or a prompt
    rule) ends the run with a polite hand-off to a colleague; the SDK records the block span.
    """
    messages: list[MessageParam] = [{"role": "user", "content": question}]
    answer = ""
    turns = 0
    blocked_by: str | None = None
    with lucentpad.trace(config.RUN_NAME, input=question, budget_usd=budget_usd) as trace:
        try:
            for turns in range(1, config.MAX_TURNS + 1):  # noqa: B007 - turns is reported
                response = client.messages.create(
                    model=model,
                    max_tokens=config.MAX_TOKENS,
                    system=config.SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                )
                answer = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason != "tool_use":
                    break
                messages.append({"role": "assistant", "content": response.content})
                results: list[ToolResultBlockParam] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    arguments: dict[str, Any] = dict(block.input)
                    content, is_error = run_tool(block.name, arguments)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "user", "content": results})
            else:
                answer = answer or (
                    "Sorry, I couldn't finish that request. A colleague will follow up."
                )
        except lucentpad.GuardrailBlocked as blocked:
            blocked_by = blocked.rule
            answer = handoff_message(blocked)
        trace.set_output(answer)
    return RunResult(answer=answer, trace_id=trace.trace_id, turns=turns, blocked_by=blocked_by)
