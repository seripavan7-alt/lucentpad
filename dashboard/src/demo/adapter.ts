/*
 * The static demo's "API": answers the dashboard's requests in the browser from a snapshot of the
 * real API's responses over the sample data (snapshot.json, written by `make demo-snapshot`).
 * It must behave like the server for every query the dashboard sends; adapter.test.ts checks it
 * against parity.json, which records the real API's paging. Any contract change to these routes
 * must be mirrored here.
 */
import type { Span, TraceDetail, TraceList, TraceSummary } from "../api/types";
import { SPAN_SOURCES, SPAN_STATUSES } from "../api/types";

export interface DemoSnapshot {
  generated_at: string;
  traces: TraceSummary[];
  spans: Record<string, Span[]>;
}

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

function shiftTime(iso: string, ms: number): string {
  return new Date(Date.parse(iso) + ms).toISOString();
}

/** Every timestamp moved by `ms`. Order and durations are unchanged. */
export function shiftSnapshot(snapshot: DemoSnapshot, ms: number): DemoSnapshot {
  if (ms === 0) return snapshot;
  const spans: Record<string, Span[]> = {};
  for (const [traceId, list] of Object.entries(snapshot.spans)) {
    spans[traceId] = list.map((s) => ({
      ...s,
      start_time: shiftTime(s.start_time, ms),
      end_time: shiftTime(s.end_time, ms),
      events: s.events?.map((e) => ({ ...e, time: shiftTime(e.time, ms) })),
    }));
  }
  return {
    generated_at: shiftTime(snapshot.generated_at, ms),
    traces: snapshot.traces.map((t) => ({ ...t, start_time: shiftTime(t.start_time, ms) })),
    spans,
  };
}

/** The shift that makes the newest trace end a minute before `now`, so the demo always looks fresh. */
export function freshShift(snapshot: DemoSnapshot, now: number = Date.now()): number {
  let newestEnd = -Infinity;
  for (const t of snapshot.traces) {
    newestEnd = Math.max(newestEnd, Date.parse(t.start_time) + t.duration_ms);
  }
  return Number.isFinite(newestEnd) ? Math.round(now - 60_000 - newestEnd) : 0;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function invalid(param: string, msg: string): Response {
  return json({ detail: [{ type: "value_error", loc: ["query", param], msg }] }, 422);
}

function isOneOf<T extends string>(values: readonly T[], value: string): value is T {
  return (values as readonly string[]).includes(value);
}

/**
 * A fetch-compatible function over the snapshot (see `setFetcher` in api/client.ts).
 * Cursors are the adapter's own: the position of the page's last trace in the full list.
 */
export function createDemoFetch(snapshot: DemoSnapshot) {
  // The snapshot's traces are already in the API's order: start_time desc, trace_id desc.
  const traces = snapshot.traces;
  const byId = new Map(traces.map((t, i) => [t.trace_id, { trace: t, index: i }]));

  function list(params: URLSearchParams): Response {
    const rawLimit = params.get("limit");
    const limit = rawLimit === null ? DEFAULT_LIMIT : Number(rawLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
      return invalid("limit", `limit must be an integer from 1 to ${MAX_LIMIT}`);
    }
    const source = params.get("source");
    if (source !== null && !isOneOf(SPAN_SOURCES, source)) return invalid("source", "bad source");
    const status = params.get("status");
    if (status !== null && !isOneOf(SPAN_STATUSES, status)) return invalid("status", "bad status");
    const rawCursor = params.get("cursor");
    let after = -1;
    if (rawCursor !== null) {
      const match = /^d([0-9]+)$/.exec(rawCursor);
      if (!match) return invalid("cursor", "invalid cursor");
      after = Number(match[1]);
    }

    const page: TraceSummary[] = [];
    let lastIndex = -1;
    let hasNext = false;
    for (let i = after + 1; i < traces.length; i++) {
      const t = traces[i];
      if (!t) break;
      if ((source !== null && t.source !== source) || (status !== null && t.status !== status)) {
        continue;
      }
      if (page.length === limit) {
        hasNext = true;
        break;
      }
      page.push(t);
      lastIndex = i;
    }
    const body: TraceList = { traces: page, next_cursor: hasNext ? `d${lastIndex}` : null };
    return json(body);
  }

  function detail(traceId: string): Response {
    const hit = byId.get(traceId);
    if (!hit) return json({ detail: "trace not found" }, 404);
    const body: TraceDetail = { trace: hit.trace, spans: snapshot.spans[traceId] ?? [] };
    return json(body);
  }

  return async function demoFetch(url: string): Promise<Response> {
    await Promise.resolve();
    const { pathname, searchParams } = new URL(url, "http://demo.invalid");
    if (pathname === "/healthz") return json({ status: "ok" });
    if (pathname === "/v1/traces") return list(searchParams);
    const traceId = /^\/v1\/traces\/([^/]+)$/.exec(pathname)?.[1];
    if (traceId !== undefined) return detail(decodeURIComponent(traceId));
    return json({ detail: "Not Found" }, 404);
  };
}
