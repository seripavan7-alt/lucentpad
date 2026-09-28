"""``--mock-llm``: scripted Claude responses served through a mock HTTP transport.

The real ``anthropic.Anthropic`` client (wrapped by LucentPad) talks to this transport instead of
the API, so runs are free, offline and repeatable, and the llm spans are the real thing. The
script follows the demo (PRD "Demo agent and script"), from the conversation so far:

- one order number: look it up, then refund it (delivered + "refund") and draft an email,
  reschedule it ("reschedule", "tomorrow", "instead"; only if it hasn't shipped), or answer
  with its status; an unknown order gets "couldn't find";
- several order numbers: look each up in turn, then summarise (a long run, for the budget);
- no order number: small talk, no tools.

Like a real model it follows one line of the system prompt: without "Always look an order up"
it answers from nowhere, without tools (the demo-break patch in ``evals/`` relies on this).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx2

MOCK_API_KEY = "mock-llm-offline"  # not a credential: the mock transport never leaves the process
RESCHEDULE_WINDOW = "tomorrow 10:00-10:30"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


@dataclass(frozen=True)
class _Call:
    name: str
    input: dict[str, Any]
    result: Any  # parsed JSON when possible
    failed: bool


def _calls(messages: list[dict[str, Any]]) -> list[_Call]:
    """Every tool call so far with its result, in order."""
    uses: dict[str, dict[str, Any]] = {}
    calls: list[_Call] = []
    for m in messages:
        if not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if m["role"] == "assistant" and b.get("type") == "tool_use":
                uses[b["id"]] = b
            elif m["role"] == "user" and b.get("type") == "tool_result":
                use = uses.get(b["tool_use_id"], {})
                content = b.get("content", "")
                raw = content if isinstance(content, str) else _text(content)
                try:
                    parsed: Any = json.loads(raw)
                except ValueError:
                    parsed = raw
                calls.append(
                    _Call(
                        use.get("name", "?"), use.get("input", {}), parsed, bool(b.get("is_error"))
                    )
                )
    return calls


def _status_line(order: dict[str, Any]) -> str:
    if order["status"] == "delivered":
        return f"Order {order['order_id']} was delivered on {order['delivered_at']}."
    return f"Order {order['order_id']} is {order['status']}; it was placed on {order['placed_at']}."


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
        system = body.get("system") or ""
        question = _text(messages[0]["content"])
        q = question.lower()
        calls = _calls(messages)

        if "look an order up" not in _text(system).lower():
            return [
                {
                    "type": "text",
                    "text": "Thanks for reaching out! Orders usually arrive within 3-5 business "
                    "days.",
                }
            ], "end_turn"

        order_ids = list(dict.fromkeys(re.findall(r"\b(\d{3,6})\b", question)))
        if not order_ids:
            return [
                {"type": "text", "text": "Happy to help! What's your order number?"}
            ], "end_turn"

        lookups = {str(c.input.get("order_id")): c for c in calls if c.name == "lookup_order"}
        pending = [i for i in order_ids if i not in lookups]
        if pending:
            return [
                {"type": "text", "text": f"Let me look up order {pending[0]}."},
                self._tool("lookup_order", {"order_id": pending[0]}),
            ], "tool_use"

        if len(order_ids) > 1:
            lines = [
                f"I couldn't find order {i}."
                if lookups[i].failed
                else _status_line(lookups[i].result)
                for i in order_ids
            ]
            return [{"type": "text", "text": " ".join(lines)}], "end_turn"

        lookup = lookups[order_ids[0]]
        if lookup.failed:
            return [
                {
                    "type": "text",
                    "text": "I couldn't find that order. Could you double-check the number?",
                }
            ], "end_turn"
        order = lookup.result
        done = {c.name: c for c in calls}

        if "issue_refund" in done:
            refund = done["issue_refund"]
            if refund.failed:
                text = "I couldn't issue that refund, so I've passed it to a colleague."
            else:
                text = (
                    f"Done! I've refunded ${refund.result['amount_usd']:.2f} for order "
                    f"{refund.result['order_id']} (3-5 business days to reach your card) and "
                    "drafted a confirmation email to you."
                )
            return [{"type": "text", "text": text}], "end_turn"

        if "reschedule_delivery" in done:
            moved = done["reschedule_delivery"]
            if moved.failed:
                text = (
                    f"Order {order['order_id']} has already left our warehouse, so I can't move "
                    "its delivery. Sorry!"
                )
            else:
                text = (
                    f"Order {order['order_id']} hasn't shipped yet, so I've rescheduled the "
                    f"delivery for {moved.result['window']}. You'll get a text when it's on the "
                    "way."
                )
            return [{"type": "text", "text": text}], "end_turn"

        status = order["status"]
        if "refund" in q and status == "delivered":
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

        if any(w in q for w in ("reschedul", "tomorrow", "instead")):
            if status != "processing":
                return [
                    {
                        "type": "text",
                        "text": f"{_status_line(order)} It has already shipped, so its delivery "
                        "can't be rescheduled.",
                    }
                ], "end_turn"
            return [
                {
                    "type": "text",
                    "text": f"Order {order['order_id']} hasn't shipped yet, so the delivery can "
                    "still move.",
                },
                self._tool(
                    "reschedule_delivery",
                    {"order_id": order["order_id"], "window": RESCHEDULE_WINDOW},
                ),
            ], "tool_use"

        return [{"type": "text", "text": _status_line(order)}], "end_turn"

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
