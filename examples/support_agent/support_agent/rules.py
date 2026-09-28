"""The built-in guardrail rules, for runs without the LucentPad API (``--local-rules``)."""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Any

import yaml


@cache
def local_rules() -> dict[str, Any]:
    """``rules.yaml``: the same rules the server serves by default (``refund_limit``)."""
    text = resources.files("support_agent").joinpath("rules.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(text)
    return data
