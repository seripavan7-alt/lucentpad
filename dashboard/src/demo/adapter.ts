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
  TraceOrder,
  TraceSort,
  TraceSummary,
} from "../api/types";
import { FACET_MAX_VALUES, FACETS, SPAN_SOURCES, SPAN_STATUSES, TRACE_SORTS } from "../api/types";

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

/** Plain code-point order, like Postgres `COLLATE "C"` (JS `<` compares UTF-16 units). */
export function compareCodePoints(a: string, b: string): number {
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const x = a.codePointAt(i) ?? 0;
    const y = b.codePointAt(j) ?? 0;
    if (x !== y) return x - y;
    i += x > 0xffff ? 2 : 1;
    j += y > 0xffff ? 2 : 1;
  }
  return i < a.length ? 1 : j < b.length ? -1 : 0;
}

/**
 * Ascending comparators per sort key, ties on trace_id. "started" is not here: the snapshot is
 * already in the API's started order, which keeps the microseconds `Date.parse` would drop.
 */
const SORT_KEYS: Record<
  Exclude<TraceSort, "started">,
  (a: TraceSummary, b: TraceSummary) => number
> = {
  duration: (a, b) => a.duration_ms - b.duration_ms,
  cost: (a, b) => a.cost_usd - b.cost_usd,
  name: (a, b) => compareCodePoints(a.name, b.name),
  source: (a, b) => compareCodePoints(a.source, b.source),
};

const compareValues = (a: FacetValue, b: FacetValue): number =>
  b.count - a.count || (a.value < b.value ? -1 : a.value > b.value ? 1 : 0);

/**
 * A fetch-compatible function over the snapshot (see `setFetcher` in api/client.ts).
 * Cursors are the adapter's own: order letter, the position of the page's last trace in the
 * sorted list, the sort key and the filter fingerprint (`d12.cost.abc`); reusing one with
 * another sort, order or filter set is a 422, like the API.
 */
export function createDemoFetch(snapshot: DemoSnapshot) {
  // The snapshot's traces are already in the API's order: start_time desc, trace_id desc.
  // Oldest first (asc) is exactly the reverse.
  const traces = snapshot.traces;
  const sorted = new Map<string, TraceSummary[]>();
  /** Traces in the API's order for a sort and direction (ties on trace_id, same direction). */
  function orderedBy(sort: TraceSort, order: TraceOrder): TraceSummary[] {
    const key = `${sort}.${order}`;
    let list = sorted.get(key);
    if (!list) {
      if (sort === "started") {
        list = order === "desc" ? traces : [...traces].reverse();
      } else {
        const compare = SORT_KEYS[sort];
        const sign = order === "desc" ? -1 : 1;
        list = [...traces].sort(
          (a, b) => sign * (compare(a, b) || compareCodePoints(a.trace_id, b.trace_id)),
        );
      }
      sorted.set(key, list);
    }
    return list;
  }
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
    const rawSort = params.get("sort") ?? "started";
    const sort = TRACE_SORTS.find((s) => s === rawSort);
    if (sort === undefined) return invalid("sort", `sort must be one of ${TRACE_SORTS.join(", ")}`);
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
      const match = /^([da])([0-9]+)\.([a-z]+)\.([0-9a-z]+)$/.exec(rawCursor);
      if (!match || match[1] !== order[0] || match[3] !== sort || match[4] !== fp) {
        return invalid("cursor", "invalid cursor for this sort, order and filter set");
      }
      after = Number(match[2]);
    }

    const ordered = orderedBy(sort, order);
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
      next_cursor: hasNext ? `${order[0]}${lastIndex}.${sort}.${fp}` : null,
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

/**
 * The demo's "API" as the dashboard sees it: the snapshot re-anchored to the viewer's clock on
 * every request, so the newest trace always ended a minute ago. Each time range therefore always
 * shows the same traces, whenever the page is opened and however long it stays open.
 *
 * Wraps `createDemoFetch` (which answers in the snapshot's own time): request times (`from`,
 * `to`, `since`) are moved back by the current shift, response times forward. A paging cursor
 * carries the shift it was issued with, so later pages use the same one and stay consistent.
 */
export function createLiveDemoFetch(snapshot: DemoSnapshot, now: () => number = Date.now) {
  const inner = createDemoFetch(snapshot);
  const anchor = freshShift(snapshot, 0); // shift = now() + anchor

  const move = (iso: string, ms: number) => shiftTime(iso, ms);
  const moveSpan = (s: Span, ms: number): Span => ({
    ...s,
    start_time: move(s.start_time, ms),
    end_time: move(s.end_time, ms),
    events: s.events?.map((e) => ({ ...e, time: move(e.time, ms) })),
  });
  const moveTrace = (t: TraceSummary, ms: number): TraceSummary => ({
    ...t,
    start_time: move(t.start_time, ms),
  });

  return async function liveDemoFetch(url: string): Promise<Response> {
    const parsed = new URL(url, "http://demo.invalid");
    const params = parsed.searchParams;
    let shift = Math.round(now() + anchor);
    const cursor = params.get("cursor");
    if (cursor !== null) {
      const at = cursor.lastIndexOf("~");
      const carried = at >= 0 ? Number(cursor.slice(at + 1)) : NaN;
      if (Number.isInteger(carried)) {
        shift = carried;
        params.set("cursor", cursor.slice(0, at));
      }
    }
    for (const name of ["from", "to", "since"]) {
      const raw = params.get(name);
      const t = raw === null ? NaN : Date.parse(raw);
      // Leave malformed values alone so the inner adapter rejects them like the API does.
      if (raw !== null && /(Z|[+-]\d{2}:?\d{2})$/i.test(raw.trim()) && !Number.isNaN(t)) {
        params.set(name, new Date(t - shift).toISOString());
      }
    }
    const res = await inner(`${parsed.pathname}${parsed.search}`);
    if (!res.ok) return res;

    const body = (await res.json()) as Record<string, unknown>;
    const asOf = new Date(now()).toISOString();
    if (Array.isArray(body.traces)) {
      const list = body as unknown as TraceList;
      return json({
        traces: list.traces.map((t) => moveTrace(t, shift)),
        next_cursor: list.next_cursor === null ? null : `${list.next_cursor}~${shift}`,
        as_of: asOf,
      } satisfies TraceList);
    }
    if (body.trace !== undefined && Array.isArray(body.spans)) {
      const detail = body as unknown as TraceDetail;
      return json({
        trace: moveTrace(detail.trace, shift),
        spans: detail.spans.map((s) => moveSpan(s, shift)),
        as_of: asOf,
      } satisfies TraceDetail);
    }
    return json(body); // facets and health carry no times
  };
}
