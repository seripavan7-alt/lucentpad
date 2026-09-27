import type { InfiniteData } from "@tanstack/react-query";
import { mergeGatewayTurns, type GatewayPage } from "../../api/queries";
import { isoMicros } from "../../lib/time";
import { CC_SESSION, CHAT_SESSION, turn, turnsPage1 } from "../../test/gatewayFixtures";
import { groupSessions, totalsByClient } from "./sessions";
import { applyGatewayPatch, parseGatewayView } from "./view";

describe("groupSessions", () => {
  it("keeps interleaved sessions in one group each, ordered by their newest turn", () => {
    const groups = groupSessions(turnsPage1.turns);
    expect(groups.map((g) => g.traceId)).toEqual([CC_SESSION, CHAT_SESSION]);
    const cc = groups[0]!;
    expect(cc.turns.map((t) => t.span_id)).toEqual([
      "aa00000000000003",
      "aa00000000000002",
      "aa00000000000001",
    ]);
    expect(cc.started).toBe(turnsPage1.turns[3]!.start_time);
    expect(cc.latest).toBe(turnsPage1.turns[0]!.start_time);
    expect(cc.costUsd).toBeCloseTo(0.0401, 10); // the null-cost turn counts as 0
    expect(cc.inputTokens).toBe(36_000);
  });
});

describe("mergeGatewayTurns", () => {
  const data: InfiniteData<GatewayPage> = {
    pages: [{ ...turnsPage1, from: "x" }],
    pageParams: [null],
  };

  it("adds unseen turns in start order, replaces known ones, and advances as_of", () => {
    const late = turn("ee00000000000001", CC_SESSION, -90_000); // stored late, started earlier
    const updated = { ...turnsPage1.turns[0]!, output_tokens: 999 };
    const merged = mergeGatewayTurns(data, {
      turns: [late, updated],
      next_cursor: null,
      as_of: "2026-09-25T10:10:03Z",
    });
    expect(merged.added).toEqual(["ee00000000000001"]);
    const page = merged.data.pages[0]!;
    expect(page.as_of).toBe("2026-09-25T10:10:03Z");
    expect(page.turns.map((t) => t.span_id)).toEqual([
      "aa00000000000003",
      "ee00000000000001",
      "bb00000000000002",
      "aa00000000000002",
      "aa00000000000001",
    ]);
    expect(page.turns[0]!.output_tokens).toBe(999);
  });
});

describe("totalsByClient", () => {
  it("returns the four clients in display order with zeros for missing ones", () => {
    const rows = totalsByClient({
      clients: [
        { client: "other", sessions: 1, turns: 2, input_tokens: 3, output_tokens: 4, cost_usd: 5 },
      ],
      as_of: "2026-09-25T10:00:00Z",
    });
    expect(rows.map((r) => [r.client, r.turns])).toEqual([
      ["claude-code", 0],
      ["copilot-chat", 0],
      ["copilot-cli", 0],
      ["other", 2],
    ]);
  });
});

describe("gateway view URL", () => {
  it("parses range and client, ignoring unknown values", () => {
    expect(parseGatewayView(new URLSearchParams("range=7d&client=copilot-cli"))).toEqual({
      range: "7d",
      client: "copilot-cli",
    });
    expect(parseGatewayView(new URLSearchParams("range=2y&client=cursor"))).toEqual({
      range: "24h",
      client: null,
    });
  });

  it("omits defaults", () => {
    const next = applyGatewayPatch(new URLSearchParams("range=1h&client=other"), {
      range: "24h",
      client: null,
    });
    expect(next.toString()).toBe("");
  });
});

describe("isoMicros", () => {
  it("keeps microseconds and handles missing fractions and offsets", () => {
    expect(isoMicros("2026-09-25T10:00:00.000123Z") - isoMicros("2026-09-25T10:00:00Z")).toBe(123);
    expect(isoMicros("2026-09-25T12:00:00.5+02:00")).toBe(isoMicros("2026-09-25T10:00:00.500Z"));
    expect(isoMicros("nope")).toBeNaN();
  });
});
