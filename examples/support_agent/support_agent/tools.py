"""The agent's three tools, over fake data. Each call is a ``kind="tool"`` span."""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import Any

from anthropic.types import ToolParam

import lucentpad

from .config import ATTR_REFUND_AMOUNT


class ToolError(Exception):
    """A tool failed in a way the model should hear about (returned as ``is_error``)."""


@cache
def _orders() -> dict[str, dict[str, Any]]:
    text = resources.files("support_agent").joinpath("orders.json").read_text(encoding="utf-8")
    data: dict[str, dict[str, Any]] = json.loads(text)
    return data


def _find_order(order_id: str) -> dict[str, Any]:
    order = _orders().get(str(order_id).strip().lstrip("#"))
    if order is None:
        raise ToolError(f"order {order_id} not found")
    return order


@lucentpad.span
def lookup_order(order_id: str) -> dict[str, Any]:
    return _find_order(order_id)


@lucentpad.span
def issue_refund(order_id: str, amount: float) -> dict[str, Any]:
    lucentpad.set_attribute(ATTR_REFUND_AMOUNT, float(amount))
    order = _find_order(order_id)
    if amount <= 0 or amount > order["total_usd"]:
        raise ToolError(f"refund of ${amount:.2f} is not within the order total")
    return {
        "refund_id": f"rf_{order['order_id']}_{round(amount * 100)}",
        "order_id": order["order_id"],
        "amount_usd": round(float(amount), 2),
        "status": "issued",
    }


@lucentpad.span
def draft_email(to: str, subject: str, body: str) -> dict[str, Any]:
    if "@" not in to:
        raise ToolError(f"not an email address: {to!r}")
    draft_id = f"draft_{zlib.crc32(f'{to}|{subject}|{body}'.encode()):08x}"
    return {"draft_id": draft_id, "to": to, "subject": subject, "status": "draft"}


TOOLS: list[ToolParam] = [
    {
        "name": "lookup_order",
        "description": "Look up an order by its number: status, items, total and customer.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. 1042"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Refund an amount (USD) on an order back to the customer's card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "description": "Amount in USD"},
            },
            "required": ["order_id", "amount"],
        },
    },
    {
        "name": "draft_email",
        "description": "Draft an email to the customer (a human reviews it before it is sent).",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]

HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "lookup_order": lookup_order,
    "issue_refund": issue_refund,
    "draft_email": draft_email,
}


def run_tool(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool the model asked for; return (result JSON, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        return json.dumps(handler(**arguments)), False
    except (ToolError, TypeError, ValueError) as exc:
        return json.dumps({"error": str(exc)}), True
