"""``evals/baseline.json``: the last accepted result of a suite (per-case pass/fail + cost)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COST_RISE_LIMIT = 1.25  # D25: total cost more than 25% over the baseline is a regression


class BaselineError(ValueError):
    """The baseline file exists but can't be used."""


@dataclass(frozen=True)
class BaselineCase:
    passed: bool
    cost_usd: float | None


@dataclass(frozen=True)
class Baseline:
    suite: str
    model: str | None
    mock: bool  # recorded with --mock (scripted model): its costs say nothing about a real one
    total_cost_usd: float | None
    cases: dict[str, BaselineCase]


def _cost(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise BaselineError(f"bad cost {value!r}")
    return float(value)


def load(path: Path) -> Baseline | None:
    """The baseline at ``path``; None when the file doesn't exist."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise BaselineError(f"cannot read {path}: {exc}") from None
    try:
        cases_raw = data["cases"]
        if not isinstance(cases_raw, dict):
            raise BaselineError("cases must be a mapping")
        cases = {
            str(k): BaselineCase(bool(v["passed"]), _cost(v.get("cost_usd")))
            for k, v in cases_raw.items()
        }
        model = data.get("model")
        return Baseline(
            suite=str(data.get("suite", "")),
            model=model if isinstance(model, str) else None,
            mock=bool(data.get("mock", False)),
            total_cost_usd=_cost(data.get("total_cost_usd")),
            cases=cases,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise BaselineError(f"{path}: malformed baseline ({exc})") from None
    except BaselineError as exc:
        raise BaselineError(f"{path}: {exc}") from None


def save(path: Path, baseline: Baseline) -> None:
    body = {
        "_about": "Written by `lucentpad eval --update-baseline`. See evals/README.md.",
        "suite": baseline.suite,
        "model": baseline.model,
        "mock": baseline.mock,
        "total_cost_usd": baseline.total_cost_usd,
        "cases": {
            k: {"passed": c.passed, "cost_usd": c.cost_usd} for k, c in baseline.cases.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def cost_comparable(baseline: Baseline, *, model: str | None, mock: bool) -> str | None:
    """None when the totals can be compared, else why not."""
    if baseline.total_cost_usd is None:
        return "the baseline has no total cost"
    if baseline.mock != mock:
        return (
            "the baseline was recorded with --mock"
            if baseline.mock
            else "this run uses --mock, the baseline a real model"
        )
    if baseline.model != model:
        return f"the baseline ran on {baseline.model or 'the default model'}"
    return None
