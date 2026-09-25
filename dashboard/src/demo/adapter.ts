/*
 * The static demo's "API": answers the dashboard's requests in the browser from a snapshot of the
 * real API's responses over the sample data (snapshot.json, written by `make demo-snapshot`).
 * It must behave like the server for every query the dashboard sends; adapter.test.ts checks it
 * against parity.json, which records the real API's paging. Any contract change to these routes
 * must be mirrored here.
 */
import type {
  Facet,
  FacetValue,
  Span,
  TraceDetail,
  TraceFacets,
  TraceList,
  TraceSummary,
} from "../api/types";
import { FACET_MAX_VALUES, FACETS, SPAN_SOURCES, SPAN_STATUSES } from "../api/types";

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

/**
 * The shift that makes the newest trace end a minute before `now`, so the demo always looks
 * fresh (and the Traces page's default 15-minute range is never empty).
 */
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

class InvalidParam extends Error {
  constructor(
    readonly param: string,
    msg: string,
  ) {
    super(msg);
  }
}

/** A tz-aware ISO timestamp as epoch ms; naive or malformed → 422 like the API. */
function parseTime(params: URLSearchParams, name: string): number | null {
  const raw = params.get(name);
  if (raw === null || raw === "") return null;
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(raw.trim())) {
    throw new InvalidParam(name, "timestamp must include a timezone");
  }
  const t = Date.parse(raw);
  if (Number.isNaN(t)) throw new InvalidParam(name, "invalid datetime");
  return t;
}

interface Filter {
  from: number | null;
  to: number | null;
  values: Record<Facet, readonly string[]>;
}

function parseFilter(params: URLSearchParams): Filter {
  const values = {} as Record<Facet, readonly string[]>;
  for (const facet of FACETS) values[facet] = params.getAll(facet);
  if (!values.source.every((v) => (SPAN_SOURCES as readonly string[]).includes(v))) {
    throw new InvalidParam("source", "bad source");
  }
  if (!values.status.every((v) => (SPAN_STATUSES as readonly string[]).includes(v))) {
    throw new InvalidParam("status", "bad status");
  }
  return { from: parseTime(params, "from"), to: parseTime(params, "to"), values };
}

/** The values a trace has for one facet (model: every model it used). */
function facetValues(t: TraceSummary, facet: Facet): string[] {
  switch (facet) {
    case "name":
      return [t.name];
    case "status":
      return [t.status];
    case "source":
      return [t.source];
    case "client":
      return t.client ? [t.client] : [];
    case "model":
      return t.models;
    case "service":
      return t.service_name ? [t.service_name] : [];
  }
}

/** OR within a filter, AND across filters; `skip` leaves one facet's own filter out. */
function matches(t: TraceSummary, filter: Filter, skip?: Facet): boolean {
  const start = Date.parse(t.start_time);
  if (filter.from !== null && start < filter.from) return false;
  if (filter.to !== null && start >= filter.to) return false;
  for (const facet of FACETS) {
    if (facet === skip) continue;
    const selected = filter.values[facet];
    if (selected.length && !facetValues(t, facet).some((v) => selected.includes(v))) return false;
  }
  return true;
}

/** Stable key of the filter set, embedded in cursors (like the API's fingerprint). */
function fingerprint(filter: Filter): string {
  const data = JSON.stringify([
    filter.from,
    filter.to,
    FACETS.map((f) => [...filter.values[f]].sort()),
  ]);
  let h = 0;
  for (let i = 0; i < data.length; i++) h = (Math.imul(h, 31) + data.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

const compareValues = (a: FacetValue, b: FacetValue): number =>
  b.count - a.count || (a.value < b.value ? -1 : a.value > b.value ? 1 : 0);

/**
 * A fetch-compatible function over the snapshot (see `setFetcher` in api/client.ts).
 * Cursors are the adapter's own: order letter, the position of the page's last trace in the
 * snapshot, and the filter fingerprint (`d12.abc`); reusing one with another order or filter
 * set is a 422, like the API.
 */
export function createDemoFetch(snapshot: DemoSnapshot) {
  // The snapshot's traces are already in the API's order: start_time desc, trace_id desc.
  // Oldest first (asc) is exactly the reverse.
  const traces = snapshot.traces;
  const byId = new Map(traces.map((t) => [t.trace_id, t]));
  const traceEnd = (t: TraceSummary): number => Date.parse(t.start_time) + t.duration_ms;

  function list(params: URLSearchParams): Response {
    const rawLimit = params.get("limit");
    const limit = rawLimit === null ? DEFAULT_LIMIT : Number(rawLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
      return invalid("limit", `limit must be an integer from 1 to ${MAX_LIMIT}`);
    }
    const order = params.get("order") ?? "desc";
    if (order !== "desc" && order !== "asc") return invalid("order", "order must be desc or asc");
    const filter = parseFilter(params);
    // Live polling: the snapshot never changes, so "gained spans after `since`" means the
    // trace ended after it (spans are exported when they end).
    const since = parseTime(params, "since");
    const rawCursor = params.get("cursor");
    if (since !== null && rawCursor !== null) {
      return invalid("cursor", "cursor cannot be combined with since");
    }
    const fp = fingerprint(filter);
    let after = -1;
    if (rawCursor !== null) {
      const match = /^([da])([0-9]+)\.([0-9a-z]+)$/.exec(rawCursor);
      if (!match || match[1] !== order[0] || match[3] !== fp) {
        return invalid("cursor", "invalid cursor for this order and filter set");
      }
      after = Number(match[2]);
    }

    const ordered = order === "desc" ? traces : [...traces].reverse();
    const page: TraceSummary[] = [];
    let lastIndex = -1;
    let hasNext = false;
    for (let i = after + 1; i < ordered.length; i++) {
      const t = ordered[i];
      if (!t) break;
      if (!matches(t, filter) || (since !== null && traceEnd(t) <= since)) continue;
      if (page.length === limit) {
        hasNext = true;
        break;
      }
      page.push(t);
      lastIndex = i;
    }
    const body: TraceList = {
      traces: page,
      next_cursor: hasNext ? `${order[0]}${lastIndex}.${fp}` : null,
      as_of: new Date().toISOString(),
    };
    return json(body);
  }

  function facets(params: URLSearchParams): Response {
    const filter = parseFilter(params);
    const body = {} as TraceFacets;
    for (const facet of FACETS) {
      const counts = new Map<string, number>();
      for (const t of traces) {
        if (!matches(t, filter, facet)) continue;
        for (const v of new Set(facetValues(t, facet))) counts.set(v, (counts.get(v) ?? 0) + 1);
      }
      body[facet] = [...counts]
        .map(([value, count]) => ({ value, count }))
        .sort(compareValues)
        .slice(0, FACET_MAX_VALUES);
    }
    return json(body);
  }

  function detail(traceId: string, params: URLSearchParams): Response {
    const trace = byId.get(traceId);
    if (!trace) return json({ detail: "trace not found" }, 404);
    const since = parseTime(params, "since");
    const spans = snapshot.spans[traceId] ?? [];
    const body: TraceDetail = {
      trace,
      spans: since === null ? spans : spans.filter((s) => Date.parse(s.end_time) > since),
      as_of: new Date().toISOString(),
    };
    return json(body);
  }

  return async function demoFetch(url: string): Promise<Response> {
    await Promise.resolve();
    const { pathname, searchParams } = new URL(url, "http://demo.invalid");
    try {
      if (pathname === "/healthz") return json({ status: "ok" });
      if (pathname === "/v1/traces") return list(searchParams);
      if (pathname === "/v1/traces/facets") return facets(searchParams);
      const traceId = /^\/v1\/traces\/([^/]+)$/.exec(pathname)?.[1];
      if (traceId !== undefined) return detail(decodeURIComponent(traceId), searchParams);
    } catch (err) {
      if (err instanceof InvalidParam) return invalid(err.param, err.message);
      throw err;
    }
    return json({ detail: "Not Found" }, 404);
  };
}
