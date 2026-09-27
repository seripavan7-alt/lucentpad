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
  GatewayClientTotals,
  GatewaySummary,
  GatewayTurn,
  GatewayTurnList,
  Span,
  TraceDetail,
  TraceFacets,
  TraceList,
  TraceOrder,
  TraceSort,
  TraceSummary,
} from "../api/types";
import {
  Attr,
  EventName,
  FACET_MAX_VALUES,
  FACETS,
  GATEWAY_PROVIDERS,
  SPAN_SOURCES,
  SPAN_STATUSES,
  TRACE_SORTS,
} from "../api/types";
import { isoMicros } from "../lib/time";

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

/** `shiftTime` that keeps the microsecond digits, so shifted turns still order exactly. */
function shiftTimeMicros(iso: string, ms: number): string {
  const micros = /\.\d{3}(\d{1,3})/.exec(iso)?.[1];
  const shifted = shiftTime(iso, ms);
  return micros ? shifted.replace(/Z$/, `${micros}Z`) : shifted;
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
  return fingerprintOf([filter.from, filter.to, FACETS.map((f) => [...filter.values[f]].sort())]);
}

function fingerprintOf(parts: unknown): string {
  const data = JSON.stringify(parts);
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

// --------------------------------------------------------------------------- gateway (M2)

type Attrs = NonNullable<Span["attributes"]>;

const str = (attrs: Attrs, key: string): string | null => {
  const v = attrs[key];
  return typeof v === "string" ? v : null;
};
const int = (attrs: Attrs, key: string): number | null => {
  const v = attrs[key];
  return typeof v === "number" && Number.isInteger(v) ? v : null;
};
const finite = (attrs: Attrs, key: string): number | null => {
  const v = attrs[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
};
const provider = (value: string | null): GatewayTurn["provider"] =>
  GATEWAY_PROVIDERS.find((p) => p === value) ?? null;

/** A gateway llm span as the API's `GatewayTurn` (see `_gateway_turn` in store.py). */
export function toGatewayTurn(span: Span): GatewayTurn {
  const attrs = span.attributes ?? {};
  const model =
    [Attr.GEN_AI_RESPONSE_MODEL, Attr.GEN_AI_REQUEST_MODEL]
      .map((k) => str(attrs, k))
      .find((v) => v !== null && v !== "") ?? null;
  return {
    trace_id: span.trace_id,
    span_id: span.span_id,
    client: str(attrs, Attr.CLIENT),
    provider:
      provider(str(attrs, Attr.GATEWAY_UPSTREAM)) ?? provider(str(attrs, Attr.GEN_AI_SYSTEM)),
    model,
    start_time: span.start_time,
    duration_ms: (isoMicros(span.end_time) - isoMicros(span.start_time)) / 1000,
    ttfb_ms: finite(attrs, Attr.TTFB_MS),
    status: span.status,
    streaming: attrs[Attr.STREAMING] === true,
    input_tokens: int(attrs, Attr.GEN_AI_INPUT_TOKENS),
    output_tokens: int(attrs, Attr.GEN_AI_OUTPUT_TOKENS),
    cost_usd: finite(attrs, Attr.COST_USD),
    failover: (span.events ?? []).some((e) => e.name === EventName.FAILOVER),
    input_preview: str(attrs, Attr.INPUT_PREVIEW),
    output_preview: str(attrs, Attr.OUTPUT_PREVIEW),
  };
}

interface GatewayRow {
  turn: GatewayTurn;
  start: number; // epoch µs
  end: number; // epoch µs
}

/** Every gateway turn in the snapshot, in the API's order: start, span_id, trace_id, all desc. */
function gatewayRows(snapshot: DemoSnapshot): GatewayRow[] {
  const rows: GatewayRow[] = [];
  for (const list of Object.values(snapshot.spans)) {
    for (const span of list) {
      if (span.source !== "gateway" || span.kind !== "llm") continue;
      rows.push({
        turn: toGatewayTurn(span),
        start: isoMicros(span.start_time),
        end: isoMicros(span.end_time),
      });
    }
  }
  const desc = (a: string, b: string) => compareCodePoints(b, a);
  return rows.sort(
    (a, b) =>
      b.start - a.start ||
      desc(a.turn.span_id, b.turn.span_id) ||
      desc(a.turn.trace_id, b.turn.trace_id),
  );
}

/** Cost sums in the API's `numeric(18, 8)` units, so totals match its decimal sums exactly. */
const COST_UNITS = 1e8;

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

  const gateway = gatewayRows(snapshot);

  /** `[from, to)` on a turn's start, in µs (request times are ms precision). */
  function inWindow(row: GatewayRow, from: number | null, to: number | null): boolean {
    if (from !== null && row.start < from * 1000) return false;
    if (to !== null && row.start >= to * 1000) return false;
    return true;
  }

  function gatewayTurns(params: URLSearchParams): Response {
    const rawLimit = params.get("limit");
    const limit = rawLimit === null ? DEFAULT_LIMIT : Number(rawLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
      return invalid("limit", `limit must be an integer from 1 to ${MAX_LIMIT}`);
    }
    const from = parseTime(params, "from");
    const to = parseTime(params, "to");
    const since = parseTime(params, "since");
    const clients = [...new Set(params.getAll("client"))].sort();
    const rawCursor = params.get("cursor");
    if (since !== null && rawCursor !== null) {
      return invalid("cursor", "cursor cannot be combined with since");
    }
    const fp = fingerprintOf([from, to, clients]);
    let after = -1;
    if (rawCursor !== null) {
      const match = /^g([0-9]+)\.([0-9a-z]+)$/.exec(rawCursor);
      if (match?.[2] !== fp) return invalid("cursor", "invalid cursor");
      after = Number(match[1]);
    }
    const page: GatewayTurn[] = [];
    let lastIndex = -1;
    let hasNext = false;
    for (let i = after + 1; i < gateway.length; i++) {
      const row = gateway[i];
      if (!row) break;
      if (!inWindow(row, from, to)) continue;
      if (clients.length && !clients.includes(row.turn.client ?? "")) continue;
      // Live polling: the snapshot never changes, so "stored after `since`" means ended after it.
      if (since !== null && row.end <= since * 1000) continue;
      if (page.length === limit) {
        hasNext = true;
        break;
      }
      page.push(row.turn);
      lastIndex = i;
    }
    const body: GatewayTurnList = {
      turns: page,
      next_cursor: hasNext && since === null ? `g${lastIndex}.${fp}` : null,
      as_of: new Date().toISOString(),
    };
    return json(body);
  }

  function gatewaySummary(params: URLSearchParams): Response {
    const from = parseTime(params, "from");
    const to = parseTime(params, "to");
    const acc = new Map<
      string,
      { sessions: Set<string>; turns: number; input: number; output: number; cost: number }
    >();
    for (const row of gateway) {
      if (!inWindow(row, from, to)) continue;
      const key = row.turn.client ?? "other";
      let a = acc.get(key);
      if (!a) {
        a = { sessions: new Set(), turns: 0, input: 0, output: 0, cost: 0 };
        acc.set(key, a);
      }
      a.sessions.add(row.turn.trace_id);
      a.turns += 1;
      a.input += row.turn.input_tokens ?? 0;
      a.output += row.turn.output_tokens ?? 0;
      a.cost += Math.round((row.turn.cost_usd ?? 0) * COST_UNITS);
    }
    const clients: GatewayClientTotals[] = [...acc]
      .map(([client, a]) => ({
        client,
        sessions: a.sessions.size,
        turns: a.turns,
        input_tokens: a.input,
        output_tokens: a.output,
        cost_usd: a.cost / COST_UNITS,
      }))
      .sort((a, b) => b.cost_usd - a.cost_usd || compareCodePoints(a.client, b.client));
    const body: GatewaySummary = { clients, as_of: new Date().toISOString() };
    return json(body);
  }

  return async function demoFetch(url: string): Promise<Response> {
    await Promise.resolve();
    const { pathname, searchParams } = new URL(url, "http://demo.invalid");
    try {
      if (pathname === "/healthz") return json({ status: "ok" });
      if (pathname === "/v1/traces") return list(searchParams);
      if (pathname === "/v1/traces/facets") return facets(searchParams);
      if (pathname === "/v1/gateway/turns") return gatewayTurns(searchParams);
      if (pathname === "/v1/gateway/summary") return gatewaySummary(searchParams);
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
    if (Array.isArray(body.turns)) {
      const list = body as unknown as GatewayTurnList;
      return json({
        turns: list.turns.map((t) => ({ ...t, start_time: shiftTimeMicros(t.start_time, shift) })),
        next_cursor: list.next_cursor === null ? null : `${list.next_cursor}~${shift}`,
        as_of: asOf,
      } satisfies GatewayTurnList);
    }
    if (Array.isArray(body.clients)) {
      return json({ ...(body as unknown as GatewaySummary), as_of: asOf } satisfies GatewaySummary);
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
