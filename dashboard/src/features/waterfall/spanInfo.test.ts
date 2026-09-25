import { describe, expect, it } from "vitest";
import type { Span } from "../../api/types";
import { spanSubtitle } from "./spanInfo";

const base: Span = {
  trace_id: "a".repeat(32),
  span_id: "b".repeat(16),
  name: "chat claude-sonnet-5",
  kind: "llm",
  source: "sdk",
  status: "ok",
  start_time: "2026-09-25T00:00:00Z",
  end_time: "2026-09-25T00:00:01Z",
  attributes: { "gen_ai.request.model": "claude-sonnet-5" },
};

describe("spanSubtitle", () => {
  it("omits the model when the span name already contains it", () => {
    expect(spanSubtitle(base)).toBeNull();
  });

  it("shows the served model when it differs from the name (failover)", () => {
    const span = { ...base, attributes: { "gen_ai.response.model": "claude-haiku-4-5" } };
    expect(spanSubtitle(span)).toBe("claude-haiku-4-5");
  });
});
