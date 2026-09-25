"""The tool-use loop over a LucentPad-wrapped Anthropic client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types import MessageParam, ToolResultBlockParam

import lucentpad

from . import config
from .tools import TOOLS, run_tool


@dataclass(frozen=True)
class RunResult:
    answer: str
    trace_id: str
    turns: int


def run(client: anthropic.Anthropic, question: str, *, model: str = config.MODEL) -> RunResult:
    """Answer one customer message. The run is one trace: agent root, llm and tool spans."""
    messages: list[MessageParam] = [{"role": "user", "content": question}]
    answer = ""
    turns = 0
    with lucentpad.trace(config.RUN_NAME, input=question) as trace:
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
            answer = answer or "Sorry, I couldn't finish that request. A colleague will follow up."
        trace.set_output(answer)
    return RunResult(answer=answer, trace_id=trace.trace_id, turns=turns)
