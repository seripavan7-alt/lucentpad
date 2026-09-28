"""A fake LucentPad API for the demo agent and ``lucentpad eval`` tests (no network, no keys).

Serves what the SDK and the eval CLI call: ``POST /v1/spans`` (validated against the contract),
``GET /v1/guardrails/rules`` (the server's built-in rules), ``GET /v1/pricing``, ``GET /healthz``,
``GET /v1/traces/{id}`` (spans with server-computed cost) and ``POST /v1/evals/runs`` (validated
with ``schema.EvalRunIn``).
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from lucentpad_server import pricing
from lucentpad_server.guardrails.rules import BUILT_IN_RULES, BUILT_IN_SOURCE, rules_version
from lucentpad_server.schema import (
    Attr,
    EvalRunIn,
    GuardrailRules,
    ModelPrice,
    PriceTable,
    SpanBatch,
)


def built_in_rules_body() -> dict[str, Any]:
    return GuardrailRules(
        rules=list(BUILT_IN_RULES), source=BUILT_IN_SOURCE, version=rules_version(BUILT_IN_RULES)
    ).model_dump(mode="json")


def price_table_body() -> dict[str, Any]:
    return PriceTable(
        prices=[
            ModelPrice(
                model=name,
                input=p.input_per_mtok,
                output=p.output_per_mtok,
                cache_read=p.cache_read_per_mtok,
                cache_write=p.cache_write_per_mtok,
            )
            for name, p in sorted(pricing.PRICES.items())
        ],
        checked=pricing.PRICES_CHECKED,
    ).model_dump(mode="json")


def span_cost(span: dict[str, Any]) -> float | None:
    """What the server stores as a span's cost (``lucentpad.cost_usd``)."""
    if span["kind"] != "llm":
        return None
    a = span["attributes"]
    model = a.get(Attr.GEN_AI_RESPONSE_MODEL) or a.get(Attr.GEN_AI_REQUEST_MODEL)
    if not isinstance(model, str):
        return None
    return pricing.cost_usd(
        model,
        int(a.get(Attr.GEN_AI_INPUT_TOKENS, 0)),
        int(a.get(Attr.GEN_AI_OUTPUT_TOKENS, 0)),
        int(a.get(Attr.GEN_AI_CACHE_READ_TOKENS, 0)),
        int(a.get(Attr.GEN_AI_CACHE_CREATION_TOKENS, 0)),
    )


@dataclass
class FakeApi:
    spans: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    rules: dict[str, Any] | None = field(default_factory=built_in_rules_body)  # None -> 501
    down: bool = False  # every request raises ConnectError
    post_run_status: int = 201
    lock: threading.Lock = field(default_factory=threading.Lock)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        with self.lock:
            self.paths.append(f"{request.method} {path}")
        if request.method == "POST" and path == "/v1/spans":
            body = json.loads(request.content)
            SpanBatch.model_validate(body)  # the contract
            with self.lock:
                self.spans.extend(body["spans"])
            return httpx.Response(202, json={"accepted": len(body["spans"])})
        if request.method == "POST" and path == "/v1/evals/runs":
            body = json.loads(request.content)
            EvalRunIn.model_validate(body)  # the contract
            self.runs.append(body)
            if self.post_run_status != 201:
                return httpx.Response(self.post_run_status, json={"detail": "nope"})
            return httpx.Response(201, json={**body, "id": uuid.uuid4().hex})
        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/guardrails/rules":
            if self.rules is None:
                return httpx.Response(501, json={"detail": "not implemented"})
            return httpx.Response(200, json=self.rules)
        if path == "/v1/pricing":
            return httpx.Response(200, json=price_table_body())
        if path.startswith("/v1/traces/"):
            return self.trace_detail(path.rsplit("/", 1)[1])
        return httpx.Response(404, json={"detail": "not found"})

    def trace_detail(self, trace_id: str) -> httpx.Response:
        with self.lock:
            spans = [dict(s) for s in self.spans if s["trace_id"] == trace_id]
        if not spans:
            return httpx.Response(404, json={"detail": "trace not found"})
        total = 0.0
        for s in spans:
            cost = span_cost(s)
            if cost is not None:
                s["attributes"] = {**s["attributes"], Attr.COST_USD: cost}
                total += cost
        return httpx.Response(
            200,
            json={"trace": {"trace_id": trace_id, "cost_usd": round(total, 8)}, "spans": spans},
        )

    def trace_spans(self, trace_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [s for s in self.spans if s["trace_id"] == trace_id]
