"""``--mock-llm``: scripted Claude responses served through a mock HTTP transport.

The real ``anthropic.Anthropic`` client (wrapped by LucentPad) talks to this transport instead of
the API, so runs are free, offline and repeatable, and the llm spans are the real thing. The
script follows the demo (PRD "Demo agent and script", step 2): look the order up, then refund it
and draft an email, then answer.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import anthropic
import httpx2

MOCK_API_KEY = "mock-llm-offline"  # not a credential: the mock transport never leaves the process


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def _tool_uses(messages: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for m in messages:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    names[b["id"]] = b["name"]
    return names


def _results(messages: list[dict[str, Any]]) -> dict[str, tuple[Any, bool]]:
    """Tool results in the last user message, by tool name: (parsed content, is_error)."""
    last = messages[-1]["content"]
    if isinstance(last, str):
        return {}
    names = _tool_uses(messages)
    out: dict[str, tuple[Any, bool]] = {}
    for b in last:
        if b.get("type") == "tool_result":
            content = b.get("content", "")
            raw = content if isinstance(content, str) else _text(content)
            try:
                parsed: Any = json.loads(raw)
            except ValueError:
                parsed = raw
            out[names.get(b["tool_use_id"], "?")] = (parsed, bool(b.get("is_error")))
    return out


class ScriptedClaude:
    """Decides the next scripted reply from the conversation so far."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self._ids = 0

    def _tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ids += 1
        return {
            "type": "tool_use",
            "id": f"toolu_mock_{self._ids:04d}",
            "name": name,
            "input": arguments,
        }

    def reply(self, body: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        messages: list[dict[str, Any]] = body["messages"]
        question = _text(messages[0]["content"])
        wants_refund = "refund" in question.lower()
        results = _results(messages)

        if not results:
            match = re.search(r"\b(\d{3,6})\b", question)
            if match is None:
                return [
                    {"type": "text", "text": "Happy to help! What's your order number?"}
                ], "end_turn"
            order_id = match.group(1)
            return [
                {"type": "text", "text": f"Let me look up order {order_id}."},
                self._tool("lookup_order", {"order_id": order_id}),
            ], "tool_use"

        if "lookup_order" in results:
            order, failed = results["lookup_order"]
            if failed:
                return [
                    {
                        "type": "text",
                        "text": "I couldn't find that order. Could you double-check the number?",
                    }
                ], "end_turn"
            status = order["status"]
            if wants_refund and status == "delivered":
                amount = order["total_usd"]
                name = order["customer"]["name"].split()[0]
                return [
                    {
                        "type": "text",
                        "text": f"Order {order['order_id']} was delivered on "
                        f"{order['delivered_at']}. I'll refund the ${amount:.2f} total.",
                    },
                    self._tool("issue_refund", {"order_id": order["order_id"], "amount": amount}),
                    self._tool(
                        "draft_email",
                        {
                            "to": order["customer"]["email"],
                            "subject": f"Your refund for order {order['order_id']}",
                            "body": f"Hi {name},\n\nWe've refunded ${amount:.2f} for order "
                            f"{order['order_id']}. It should reach your card in 3-5 business "
                            "days.\n\nLucent Coffee Gear support",
                        },
                    ),
                ], "tool_use"
            where = (
                f"was delivered on {order['delivered_at']}"
                if status == "delivered"
                else (f"is {status}; it was placed on {order['placed_at']}")
            )
            return [{"type": "text", "text": f"Order {order['order_id']} {where}."}], "end_turn"

        refund, refund_failed = results.get("issue_refund", ({}, True))
        if refund_failed:
            text = "I couldn't issue that refund, so I've passed it to a colleague."
        else:
            text = (
                f"Done! I've refunded ${refund['amount_usd']:.2f} for order {refund['order_id']} "
                "(3-5 business days to reach your card) and drafted a confirmation email to you."
            )
        return [{"type": "text", "text": text}], "end_turn"

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/v1/messages":
            return httpx2.Response(
                404, json={"type": "error", "error": {"type": "not_found_error"}}
            )
        body = json.loads(request.content)
        content, stop = self.reply(body)
        if self.delay:
            time.sleep(self.delay)
        message = {
            "id": f"msg_mock_{self._ids:04d}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                # plausible, deterministic counts (~4 characters per token)
                "input_tokens": len(request.content) // 4,
                "output_tokens": max(1, len(json.dumps(content)) // 4),
            },
        }
        return httpx2.Response(200, json=message)


def mock_client(delay: float = 0.0) -> anthropic.Anthropic:
    """A real Anthropic client whose HTTP goes to ``ScriptedClaude`` instead of the network."""
    script = ScriptedClaude(delay)
    return anthropic.Anthropic(
        api_key=MOCK_API_KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(script.handler)),
    )
