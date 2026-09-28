/*
 * Turns a `CostSeries` (one point per bucket × group with any spend) into what the Costs chart
 * draws: every bucket in the window as a column (empty ones included, so time reads evenly),
 * the groups as stacked series in a fixed colour order, and the totals for the range.
 */
import type { CostGroup, CostSeries } from "../../api/types";
import { clientLabel } from "../../lib/format";

/** Named series the chart colours; any more fold into "Other" (dataviz: never cycle hues). */
export const MAX_SERIES = 6;
export const OTHER_KEY = "\u0000other";

/**
 * Colour slots follow the entity, not its rank, so a range change doesn't repaint a model:
 * well-known names keep their slot; anything else takes the free slots by spend.
 */
const KNOWN_ORDER: Record<CostGroup, readonly string[]> = {
  model: ["claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5-5", "gpt-5", "gpt-5-mini"],
  client: ["sdk", "claude-code", "copilot-chat", "copilot-cli"],
  service: [],
};

/** A series slot's fill (CSS custom properties from tokens.css). */
export const seriesColor = (slot: number): string =>
  slot === 0 ? "var(--chart-other)" : `var(--chart-${slot})`;

export interface ChartGroup {
  key: string;
  label: string;
  /** 1..MAX_SERIES, or 0 for "Other" (neutral grey). */
  slot: number;
  cost: number;
  calls: number;
  inputTokens: number;
  outputTokens: number;
}

export interface ChartColumn {
  /** Bucket start, epoch ms. */
  start: number;
  total: number;
  /** Spend per group key, in the groups' stacking order (bottom first); zeros left out. */
  segments: { key: string; cost: number }[];
}

export interface CostChartData {
  groups: ChartGroup[];
  columns: ChartColumn[];
  bucketMs: number;
  total: number;
  calls: number;
  inputTokens: number;
  outputTokens: number;
}

export function groupLabel(group: CostGroup, value: string): string {
  if (value === OTHER_KEY || value === "other") return "Other";
  if (group === "client") return clientLabel(value) ?? value;
  return value;
}

/** Sum in the API's `numeric(18, 8)` units so totals don't drift by float error. */
const UNITS = 1e8;
const units = (usd: number) => Math.round(usd * UNITS);

export function buildCostChart(series: CostSeries, from: number, to: number): CostChartData {
  const bucketMs = Math.max(1, series.bucket_seconds) * 1000;

  // Per-group totals; the server's own "other" (unknown) joins the folded tail.
  const acc = new Map<string, { cost: number; calls: number; input: number; output: number }>();
  for (const p of series.points) {
    const key = p.group === "other" ? OTHER_KEY : p.group;
    const a = acc.get(key) ?? { cost: 0, calls: 0, input: 0, output: 0 };
    a.cost += units(p.cost_usd);
    a.calls += p.calls;
    a.input += p.input_tokens;
    a.output += p.output_tokens;
    acc.set(key, a);
  }
  const ranked = [...acc.keys()]
    .filter((k) => k !== OTHER_KEY)
    .sort((a, b) => (acc.get(b)?.cost ?? 0) - (acc.get(a)?.cost ?? 0) || (a < b ? -1 : 1));
  const hasOther = acc.has(OTHER_KEY);
  const named =
    ranked.length + (hasOther ? 1 : 0) > MAX_SERIES
      ? ranked.slice(0, MAX_SERIES - 1)
      : ranked.slice(0, MAX_SERIES);
  const namedSet = new Set(named);
  const fold = (key: string) => (namedSet.has(key) ? key : OTHER_KEY);

  // Colour slots: known names first (their fixed slot), then the rest by spend.
  const slots = new Map<string, number>();
  const known = KNOWN_ORDER[series.group_by];
  const used = new Set<number>();
  for (const key of named) {
    const i = known.indexOf(key);
    if (i >= 0 && i < MAX_SERIES) {
      slots.set(key, i + 1);
      used.add(i + 1);
    }
  }
  let next = 1;
  for (const key of named) {
    if (slots.has(key)) continue;
    while (used.has(next)) next++;
    slots.set(key, next);
    used.add(next);
  }

  const groups: ChartGroup[] = named.map((key) => {
    const a = acc.get(key) ?? { cost: 0, calls: 0, input: 0, output: 0 };
    return {
      key,
      label: groupLabel(series.group_by, key),
      slot: slots.get(key) ?? 0,
      cost: a.cost / UNITS,
      calls: a.calls,
      inputTokens: a.input,
      outputTokens: a.output,
    };
  });
  const tail = [...acc].filter(([key]) => !namedSet.has(key));
  if (tail.length) {
    const sum = tail.reduce(
      (s, [, a]) => ({
        cost: s.cost + a.cost,
        calls: s.calls + a.calls,
        input: s.input + a.input,
        output: s.output + a.output,
      }),
      { cost: 0, calls: 0, input: 0, output: 0 },
    );
    groups.push({
      key: OTHER_KEY,
      label: "Other",
      slot: 0,
      cost: sum.cost / UNITS,
      calls: sum.calls,
      inputTokens: sum.input,
      outputTokens: sum.output,
    });
  }
  const order = new Map(groups.map((g, i) => [g.key, i]));

  // Columns: every aligned bucket from the window start to its end, plus any stray point.
  const byBucket = new Map<number, Map<string, number>>();
  const first = Math.floor(from / bucketMs) * bucketMs;
  if (Number.isFinite(first) && to > first) {
    for (let t = first; t < to && byBucket.size < 2000; t += bucketMs) byBucket.set(t, new Map());
  }
  for (const p of series.points) {
    const start = Date.parse(p.bucket);
    const key = fold(p.group === "other" ? OTHER_KEY : p.group);
    const cell = byBucket.get(start) ?? new Map<string, number>();
    cell.set(key, (cell.get(key) ?? 0) + units(p.cost_usd));
    byBucket.set(start, cell);
  }
  const columns: ChartColumn[] = [...byBucket]
    .sort(([a], [b]) => a - b)
    .map(([start, cell]) => {
      const segments = [...cell]
        .filter(([, cost]) => cost > 0)
        .sort(([a], [b]) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
        .map(([key, cost]) => ({ key, cost: cost / UNITS }));
      const total = [...cell.values()].reduce((s, c) => s + c, 0) / UNITS;
      return { start, total, segments };
    });

  const total = groups.reduce((s, g) => s + units(g.cost), 0) / UNITS;
  return {
    groups,
    columns,
    bucketMs,
    total,
    calls: groups.reduce((s, g) => s + g.calls, 0),
    inputTokens: groups.reduce((s, g) => s + g.inputTokens, 0),
    outputTokens: groups.reduce((s, g) => s + g.outputTokens, 0),
  };
}

/** A "nice" axis maximum and step (1, 2, 2.5 or 5 × 10ⁿ) with about `count` intervals. */
export function niceScale(max: number, count = 4): { max: number; step: number } {
  if (!(max > 0) || !Number.isFinite(max)) return { max: 1, step: 0.25 };
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
  return { max: Math.ceil(max / step - 1e-9) * step, step };
}

/** Axis tick money: as few decimals as the step needs ("$0", "$0.25", "$1.5", "$20"). */
export function formatAxisCost(value: number, step: number): string {
  let decimals = 0;
  while (decimals < 6 && Math.abs(Math.round(step * 10 ** decimals) - step * 10 ** decimals) > 1e-6)
    decimals++;
  return value === 0 ? "$0" : `$${value.toFixed(decimals)}`;
}

const HOUR = 3600_000;
const DAY = 24 * HOUR;

/** Bucket label for the axis and tooltip: a time of day for sub-day buckets, else a date. */
export function formatBucket(start: number, bucketMs: number, withDate = false): string {
  const d = new Date(start);
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  if (bucketMs >= DAY) return date;
  const time = d.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return withDate ? `${date}, ${time}` : time;
}

/** "Sep 24, 14:00 – 15:00" / "Sep 24" for the tooltip heading. */
export function formatBucketRange(start: number, bucketMs: number): string {
  if (bucketMs >= DAY) return formatBucket(start, bucketMs);
  return `${formatBucket(start, bucketMs, true)} – ${formatBucket(start + bucketMs, bucketMs)}`;
}

/** "5-minute", "1-hour", "1-day" buckets, for the chart's caption. */
export function bucketPhrase(bucketMs: number): string {
  if (bucketMs % DAY === 0) return `${bucketMs / DAY}-day`;
  if (bucketMs % HOUR === 0) return `${bucketMs / HOUR}-hour`;
  return `${Math.round(bucketMs / 60_000)}-minute`;
}

/** Which columns get an axis label: about `count` of them, evenly spaced. */
export function labelEvery(columns: number, count = 6): number {
  return Math.max(1, Math.ceil(columns / count));
}
