"""The agent's tools, over fake data. Each call is a ``kind="tool"`` span.

Every tool records its arguments and result as the span's input/output previews (JSON), so the
customer's email in ``lookup_order``'s result and ``draft_email``'s arguments reaches the SDK,
which redacts it to ``[REDACTED:email]`` before export (demo step 3). ``issue_refund`` is
guarded by the ``refund_limit`` rule: on a block the SDK raises ``lucentpad.GuardrailBlocked``
before the body runs (demo step 4).
"""

from __future__ import annotations

import functools
import inspect
import json
import zlib
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import Any

from anthropic.types import ToolParam

import lucentpad

from .config import ATTR_INPUT_PREVIEW, ATTR_OUTPUT_PREVIEW, ATTR_REFUND_AMOUNT

_capture = True


def set_capture(enabled: bool) -> None:
    """``--no-content``: don't record tool arguments and results as previews."""
    global _capture
    _capture = enabled


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


def _recorded[**P](func: Callable[P, dict[str, Any]]) -> Callable[P, dict[str, Any]]:
    """Record the call's arguments and result as the current span's previews."""
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        if _capture:
            bound = sig.bind_partial(*args, **kwargs)
            lucentpad.set_attribute(ATTR_INPUT_PREVIEW, json.dumps(bound.arguments))
        result = func(*args, **kwargs)
        if _capture:
            lucentpad.set_attribute(ATTR_OUTPUT_PREVIEW, json.dumps(result))
        return result

    return wrapper


@lucentpad.span
@_recorded
def lookup_order(order_id: str) -> dict[str, Any]:
    return _find_order(order_id)


@lucentpad.span
@_recorded
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
@_recorded
def draft_email(to: str, subject: str, body: str) -> dict[str, Any]:
    if "@" not in to:
        raise ToolError(f"not an email address: {to!r}")
    draft_id = f"draft_{zlib.crc32(f'{to}|{subject}|{body}'.encode()):08x}"
    return {"draft_id": draft_id, "to": to, "subject": subject, "status": "draft"}


@lucentpad.span
@_recorded
def reschedule_delivery(order_id: str, window: str) -> dict[str, Any]:
    order = _find_order(order_id)
    if order["status"] != "processing":
        raise ToolError(
            f"order {order['order_id']} is {order['status']}; only orders that haven't shipped "
            "can be rescheduled"
        )
    if not window.strip():
        raise ToolError("window is empty")
    return {"order_id": order["order_id"], "window": window.strip(), "status": "rescheduled"}


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
    {
        "name": "reschedule_delivery",
        "description": "Move the delivery of an order that hasn't shipped yet to a new window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "window": {
                    "type": "string",
                    "description": "The new delivery window, e.g. 'tomorrow 10:00-10:30'",
                },
            },
            "required": ["order_id", "window"],
        },
    },
]

HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "lookup_order": lookup_order,
    "issue_refund": issue_refund,
    "draft_email": draft_email,
    "reschedule_delivery": reschedule_delivery,
}


def run_tool(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool the model asked for; return (result JSON, is_error).

    ``lucentpad.GuardrailBlocked`` is not caught here: the agent hands the customer off."""
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        return json.dumps(handler(**arguments)), False
    except (ToolError, TypeError, ValueError) as exc:
        return json.dumps({"error": str(exc)}), True
