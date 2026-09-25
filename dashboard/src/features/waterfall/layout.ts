import type { Span, SpanEvent } from "../../api/types";

export interface EventMarker {
  event: SpanEvent;
  /** Milliseconds from trace start. */
  offsetMs: number;
  /** Position on the trace timeline, 0–100. */
  offsetPct: number;
}

export interface WaterfallRow {
  span: Span;
  depth: number;
  /** Milliseconds from trace start to span start. */
  startMs: number;
  durationMs: number;
  /** Bar geometry as percentages of the trace timeline, 0–100. */
  offsetPct: number;
  widthPct: number;
  childCount: number;
  events: EventMarker[];
}

export interface WaterfallLayout {
  rows: WaterfallRow[];
  /** Epoch ms of the earliest span start. */
  traceStart: number;
  /** Epoch ms of the latest span end (or `until`, if later). */
  traceEnd: number;
  totalMs: number;
}

export interface LayoutOptions {
  /**
   * Extend the timeline to this epoch ms. Lets a live (still running) trace keep
   * a stable axis as spans stream in.
   */
  until?: number;
}

const ms = (iso: string): number => Date.parse(iso);

function compareSpans(a: Span, b: Span): number {
  return ms(a.start_time) - ms(b.start_time) || a.span_id.localeCompare(b.span_id);
}

const clampPct = (v: number): number => Math.min(100, Math.max(0, v));

/**
 * Lay spans out as a waterfall: depth-first by parent, siblings by start time.
 *
 * Accepts spans in any order and tolerates partial traces (as in a live trace):
 * a span whose parent hasn't arrived yet is shown as a root until it does.
 */
export function buildWaterfall(
  spans: readonly Span[],
  options: LayoutOptions = {},
): WaterfallLayout {
  if (spans.length === 0) return { rows: [], traceStart: 0, traceEnd: 0, totalMs: 0 };

  const byId = new Map<string, Span>();
  for (const span of spans) byId.set(span.span_id, span);

  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const span of byId.values()) {
    const parent = span.parent_span_id;
    if (parent && parent !== span.span_id && byId.has(parent)) {
      const list = children.get(parent);
      if (list) list.push(span);
      else children.set(parent, [span]);
    } else {
      roots.push(span);
    }
  }

  let traceStart = Infinity;
  let traceEnd = -Infinity;
  for (const span of byId.values()) {
    traceStart = Math.min(traceStart, ms(span.start_time));
    traceEnd = Math.max(traceEnd, ms(span.end_time));
  }
  if (options.until !== undefined) traceEnd = Math.max(traceEnd, options.until);
  const totalMs = traceEnd - traceStart;
  const pct = (offset: number): number => (totalMs > 0 ? clampPct((offset / totalMs) * 100) : 0);

  const rows: WaterfallRow[] = [];
  const visited = new Set<string>();

  const visit = (span: Span, depth: number): void => {
    if (visited.has(span.span_id)) return;
    visited.add(span.span_id);
    const startMs = ms(span.start_time) - traceStart;
    const durationMs = Math.max(0, ms(span.end_time) - ms(span.start_time));
    const kids = (children.get(span.span_id) ?? []).sort(compareSpans);
    const offsetPct = pct(startMs);
    rows.push({
      span,
      depth,
      startMs,
      durationMs,
      offsetPct,
      widthPct: totalMs > 0 ? Math.min(100 - offsetPct, (durationMs / totalMs) * 100) : 100,
      childCount: kids.length,
      events: (span.events ?? [])
        .map((event) => {
          const offsetMs = ms(event.time) - traceStart;
          return { event, offsetMs, offsetPct: pct(offsetMs) };
        })
        .sort((a, b) => a.offsetMs - b.offsetMs),
    });
    for (const kid of kids) visit(kid, depth + 1);
  };

  for (const root of roots.sort(compareSpans)) visit(root, 0);
  // Spans caught in a parent cycle are unreachable from any root; show them flat at the end.
  for (const span of [...byId.values()].sort(compareSpans)) visit(span, 0);

  return { rows, traceStart, traceEnd, totalMs };
}

export interface AxisTick {
  ms: number;
  pct: number;
}

/** Evenly spaced "nice" ticks (1/2/5 × 10^n ms) across the trace duration. */
export function timeTicks(totalMs: number, target = 5): AxisTick[] {
  if (totalMs <= 0) return [{ ms: 0, pct: 0 }];
  const rough = totalMs / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? rough;
  const ticks: AxisTick[] = [];
  for (let t = 0; t <= totalMs + 1e-9; t += step) ticks.push({ ms: t, pct: (t / totalMs) * 100 });
  return ticks;
}
