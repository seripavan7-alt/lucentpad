import { describe, expect, it } from "vitest";
import type { Span } from "../../api/types";
import { at, supportSpans } from "../../test/fixtures";
import { buildWaterfall, timeTicks } from "./layout";

const ids = (spans: { span: Span }[]) => spans.map((r) => r.span.span_id);

describe("buildWaterfall", () => {
  it("orders depth-first with siblings by start time, regardless of input order", () => {
    const shuffled = [...supportSpans].reverse();
    const { rows } = buildWaterfall(shuffled);
    expect(ids(rows)).toEqual([
      "a000000000000001",
      "a000000000000002",
      "a000000000000003",
      "a000000000000004",
      "a000000000000005",
      "a000000000000006", // child of issue_refund, directly after its parent
      "a000000000000007",
    ]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 1, 1, 1, 2, 1]);
    expect(rows[0]!.childCount).toBe(5);
    expect(rows[4]!.childCount).toBe(1);
  });

  it("computes bar geometry relative to the trace bounds", () => {
    const layout = buildWaterfall(supportSpans);
    expect(layout.totalMs).toBe(4200);
    const root = layout.rows[0]!;
    expect(root.offsetPct).toBe(0);
    expect(root.widthPct).toBe(100);

    const lookup = layout.rows.find((r) => r.span.name === "lookup_order")!;
    expect(lookup.startMs).toBe(1300);
    expect(lookup.durationMs).toBe(120);
    expect(lookup.offsetPct).toBeCloseTo((1300 / 4200) * 100);
    expect(lookup.widthPct).toBeCloseTo((120 / 4200) * 100);
  });

  it("positions event markers on the trace timeline", () => {
    const { rows } = buildWaterfall(supportSpans);
    const [budget] = rows[0]!.events;
    expect(budget!.event.name).toBe("lucentpad.budget.alert");
    expect(budget!.offsetMs).toBe(3900);
    expect(budget!.offsetPct).toBeCloseTo((3900 / 4200) * 100);
  });

  it("shows spans whose parent hasn't arrived yet as roots", () => {
    const partial = supportSpans.filter((s) => s.span_id !== "a000000000000005");
    const { rows } = buildWaterfall(partial);
    const orphan = rows.find((r) => r.span.span_id === "a000000000000006")!;
    expect(orphan.depth).toBe(0);
    expect(rows).toHaveLength(partial.length);
  });

  it("grows as spans are appended (live traces)", () => {
    const first = buildWaterfall(supportSpans.slice(0, 3));
    const all = buildWaterfall(supportSpans);
    expect(first.rows).toHaveLength(3);
    expect(all.rows).toHaveLength(supportSpans.length);
    // Root spans the whole trace in both, so its geometry is stable.
    expect(first.rows[0]!.widthPct).toBe(100);
  });

  it("extends the axis with `until` for in-progress traces", () => {
    const layout = buildWaterfall(supportSpans, { until: Date.parse(at(8400)) });
    expect(layout.totalMs).toBe(8400);
    expect(layout.rows[0]!.widthPct).toBeCloseTo(50);
  });

  it("survives parent cycles and zero-length traces", () => {
    const base = supportSpans[2]!;
    const a: Span = { ...base, span_id: "f000000000000001", parent_span_id: "f000000000000002" };
    const b: Span = { ...base, span_id: "f000000000000002", parent_span_id: "f000000000000001" };
    const { rows, totalMs } = buildWaterfall([a, b]);
    expect(rows).toHaveLength(2);
    expect(totalMs).toBe(120);

    const instant: Span = { ...base, end_time: base.start_time };
    const single = buildWaterfall([instant]);
    expect(single.totalMs).toBe(0);
    expect(single.rows[0]!.widthPct).toBe(100);
  });

  it("returns an empty layout for no spans", () => {
    expect(buildWaterfall([]).rows).toEqual([]);
  });
});

describe("timeTicks", () => {
  it("uses 1/2/5 steps", () => {
    expect(timeTicks(4200).map((t) => t.ms)).toEqual([0, 1000, 2000, 3000, 4000]);
    expect(timeTicks(95_000).map((t) => t.ms)).toEqual([0, 20_000, 40_000, 60_000, 80_000]);
    expect(timeTicks(0)).toEqual([{ ms: 0, pct: 0 }]);
  });
});

describe("sibling order", () => {
  it("orders spans that start in the same millisecond by their microseconds", () => {
    const base = { trace_id: "t".repeat(32), source: "sdk" as const, status: "ok" as const };
    const root = {
      ...base,
      span_id: "f000000000000000",
      parent_span_id: null,
      name: "root",
      kind: "agent" as const,
      start_time: "2026-09-25T18:21:07.000000Z",
      end_time: "2026-09-25T18:21:11.000000Z",
    };
    const child = (span_id: string, name: string, start: string) => ({
      ...base,
      span_id,
      parent_span_id: root.span_id,
      name,
      kind: "tool" as const,
      start_time: start,
      end_time: "2026-09-25T18:21:10.200000Z",
    });
    const rows = buildWaterfall([
      root,
      child("c000000000000000", "third", "2026-09-25T18:21:10.133568Z"),
      child("a000000000000000", "second", "2026-09-25T18:21:10.133550Z"),
      child("b000000000000000", "first", "2026-09-25T18:21:10.133525Z"),
    ]).rows.map((r) => r.span.name);
    expect(rows).toEqual(["root", "first", "second", "third"]);
  });
});
