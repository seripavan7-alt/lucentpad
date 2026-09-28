import { describe, expect, it } from "vitest";
import type { CostPoint, CostSeries } from "../../api/types";
import {
  bucketPhrase,
  buildCostChart,
  formatAxisCost,
  MAX_SERIES,
  niceScale,
  OTHER_KEY,
} from "./series";

const HOUR = 3600_000;
const T0 = Date.parse("2026-09-25T00:00:00Z");

const point = (hour: number, group: string, cost: number): CostPoint => ({
  bucket: new Date(T0 + hour * HOUR).toISOString(),
  group,
  cost_usd: cost,
  calls: 1,
  input_tokens: 10,
  output_tokens: 1,
});

const series = (points: CostPoint[], group_by: CostSeries["group_by"] = "model"): CostSeries => ({
  points,
  group_by,
  bucket_seconds: 3600,
  total_cost_usd: points.reduce((n, p) => n + p.cost_usd, 0),
  as_of: new Date(T0 + 24 * HOUR).toISOString(),
});

describe("buildCostChart", () => {
  it("fills every bucket of the window and stacks groups biggest first", () => {
    const chart = buildCostChart(
      series([point(1, "gpt-5", 0.1), point(1, "claude-sonnet-5", 0.3), point(5, "gpt-5", 0.4)]),
      T0 + 30 * 60_000, // mid-bucket: the column starts at the hour
      T0 + 6 * HOUR,
    );
    expect(chart.columns.map((c) => c.start)).toEqual([0, 1, 2, 3, 4, 5].map((h) => T0 + h * HOUR));
    expect(chart.groups.map((g) => [g.key, g.cost])).toEqual([
      ["gpt-5", 0.5],
      ["claude-sonnet-5", 0.3],
    ]);
    expect(chart.columns[1]!.segments.map((s) => s.key)).toEqual(["gpt-5", "claude-sonnet-5"]);
    expect(chart.columns[1]!.total).toBeCloseTo(0.4, 10);
    expect(chart.total).toBe(0.8);
    expect(chart.calls).toBe(3);
  });

  it("keeps well-known names on their colour slot whatever their rank", () => {
    const a = buildCostChart(
      series([point(0, "gpt-5", 1), point(0, "claude-sonnet-5", 0.1)]),
      T0,
      T0 + HOUR,
    );
    const b = buildCostChart(series([point(0, "gpt-5", 0.1)]), T0, T0 + HOUR);
    const slot = (c: typeof a, key: string) => c.groups.find((g) => g.key === key)?.slot;
    expect(slot(a, "claude-sonnet-5")).toBe(1);
    expect(slot(a, "gpt-5")).toBe(4);
    expect(slot(b, "gpt-5")).toBe(4);
  });

  it("folds groups past the palette, and the server's `other`, into one grey Other", () => {
    const names = ["a", "b", "c", "d", "e", "f", "g", "h"];
    const chart = buildCostChart(
      series([...names.map((n, i) => point(0, n, 10 - i)), point(1, "other", 0.5)], "service"),
      T0,
      T0 + 2 * HOUR,
    );
    expect(chart.groups).toHaveLength(MAX_SERIES);
    const other = chart.groups.at(-1)!;
    expect(other).toMatchObject({ key: OTHER_KEY, label: "Other", slot: 0 });
    expect(other.cost).toBeCloseTo(5 + 4 + 3 + 0.5, 10);
    expect(new Set(chart.groups.map((g) => g.slot)).size).toBe(MAX_SERIES);
    expect(chart.total).toBeCloseTo(names.reduce((n, _, i) => n + 10 - i, 0) + 0.5, 10);
  });

  it("labels clients by name", () => {
    const chart = buildCostChart(series([point(0, "claude-code", 1)], "client"), T0, T0 + HOUR);
    expect(chart.groups[0]!.label).toBe("Claude Code");
  });
});

describe("axis helpers", () => {
  it("rounds the axis to a nice maximum and step", () => {
    expect(niceScale(0.73)).toEqual({ max: 0.8, step: 0.2 });
    expect(niceScale(1)).toEqual({ max: 1, step: 0.25 });
    expect(niceScale(9.2)).toEqual({ max: 10, step: 2.5 });
    expect(niceScale(0)).toEqual({ max: 1, step: 0.25 });
  });

  it("prints ticks with as few decimals as the step needs", () => {
    expect(formatAxisCost(0, 0.25)).toBe("$0");
    expect(formatAxisCost(0.5, 0.25)).toBe("$0.50");
    expect(formatAxisCost(0.4, 0.2)).toBe("$0.4");
    expect(formatAxisCost(20, 5)).toBe("$20");
    expect(formatAxisCost(0.0025, 0.0025)).toBe("$0.0025");
  });

  it("names bucket sizes", () => {
    expect(bucketPhrase(300_000)).toBe("5-minute");
    expect(bucketPhrase(3 * HOUR)).toBe("3-hour");
    expect(bucketPhrase(24 * HOUR)).toBe("1-day");
  });
});
