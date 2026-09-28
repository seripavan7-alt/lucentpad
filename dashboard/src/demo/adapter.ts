/*
 * The static demo's "API": answers the dashboard's requests in the browser from a snapshot of the
 * real API's responses over the sample data (snapshot.json, written by `make demo-snapshot`).
 * It must behave like the server for every query the dashboard sends; adapter.test.ts checks it
 * against parity.json, which records the real API's paging. Any contract change to these routes
 * must be mirrored here.
 */
import type {
  CostGroup,
  CostPoint,
  CostSeries,
  EvalRun,
  EvalRunList,
  EvalRunSummary,
  Facet,
  FacetValue,
  GatewayClientTotals,
  GatewaySummary,
  GatewayTurn,
  GatewayTurnList,
  GuardrailEvent,
  GuardrailEventList,
  GuardrailRules,
  GuardrailSummary,
  PriceTable,
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
  COST_GROUPS,
  EventName,
  FACET_MAX_VALUES,
  FACETS,
  GATEWAY_PROVIDERS,
  GUARDRAIL_EVENT_KINDS,
  SPAN_SOURCES,
  SPAN_STATUSES,
  TRACE_SORTS,
} from "../api/types";
import { isoMicros } from "../lib/time";

export interface DemoSnapshot {
  generated_at: string;
  traces: TraceSummary[];
  spans: Record<string, Span[]>;
  /** Sample eval runs (M3), newest first like the API; absent in older snapshots. */
  eval_runs?: EvalRun[];
  /** The API's active rules when the snapshot was taken (M3). */
  guardrail_rules?: GuardrailRules;
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
    ...(snapshot.guardrail_rules && { guardrail_rules: snapshot.guardrail_rules }),
    ...(snapshot.eval_runs && {
      eval_runs: snapshot.eval_runs.map((r) => ({ ...r, started_at: shiftTime(r.started_at, ms) })),
    }),
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

/** A boolean query param as FastAPI reads one; absent = false. */
function parseBool(params: URLSearchParams, name: string): boolean {
  const raw = params.get(name);
  if (raw === null) return false;
  const v = raw.toLowerCase();
  if (["true", "1", "yes", "on", "t", "y"].includes(v)) return true;
  if (["false", "0", "no", "off", "f", "n"].includes(v)) return false;
  throw new InvalidParam(name, "Input should be a valid boolean");
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

// --------------------------------------------------------------------------- M3

/** The server's price table (`pricing.py`, USD per million tokens), sorted by model. */
export const DEMO_PRICES: PriceTable = {
  checked: "2026-09-25",
  prices: [
    { model: "claude-haiku-4-5", input: 1.0, output: 5.0, cache_read: 0.1, cache_write: 1.25 },
    { model: "claude-opus-5-5", input: 4.0, output: 20.0, cache_read: 0.2, cache_write: 5.0 },
    { model: "claude-sonnet-5", input: 2.0, output: 10.0, cache_read: 0.2, cache_write: 2.5 },
    { model: "gpt-5", input: 1.25, output: 10.0, cache_read: 0.125, cache_write: 1.25 },
    { model: "gpt-5-mini", input: 0.25, output: 2.0, cache_read: 0.025, cache_write: 0.25 },
  ],
};

/** Rules for a snapshot without `guardrail_rules` (the server's built-in refund limit). */
export const DEMO_RULES: GuardrailRules = {
  source: "built-in",
  version: "built-in",
  rules: [
    {
      id: "refund_limit",
      type: "tool",
      description: "Demo support agent: refunds above the automatic limit need a person.",
      keywords: [],
      pattern: null,
      tool: "issue_refund",
      condition: "amount > 200",
      message: "Refunds over $200 need a human to approve them.",
    },
  ],
};

/** ISO time to the second, like the API's for whole-second values ("…T10:00:00Z"). */
const isoSeconds = (ms: number) => new Date(ms).toISOString().replace(/\.000Z$/, "Z");

interface EventRow {
  event: GuardrailEvent;
  time: number; // epoch µs
  stored: number; // epoch µs: when the span holding it ended (was exported)
  /** 0 for a block; a redaction's or budget alert's 1-based position in its span's events. */
  seq: number;
}

/** A redaction count as the API stores it: a number, rounded and clamped; otherwise 1. */
function redactionCount(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 1;
  return Math.min(Math.max(Math.round(value), 0), 2 ** 31 - 1);
}

/** The budget fields every non-budget event carries (null), like the API. */
const NO_BUDGET = { budget_limit_usd: null, budget_spent_usd: null, budget_scope: null } as const;

/**
 * Every block (a `kind=guardrail` span), redaction (a `lucentpad.redaction` event) and budget
 * alert (a `lucentpad.budget.alert` event) in the snapshot, like the `guardrail_events` rows the
 * server writes at ingest, newest first by (time, trace_id, span_id, seq).
 */
function guardrailRows(snapshot: DemoSnapshot): EventRow[] {
  const rows: EventRow[] = [];
  for (const list of Object.values(snapshot.spans)) {
    for (const span of list) {
      const attrs = span.attributes ?? {};
      const client = str(attrs, Attr.CLIENT);
      const stored = isoMicros(span.end_time);
      if (span.kind === "guardrail") {
        rows.push({
          time: isoMicros(span.start_time),
          stored,
          seq: 0,
          event: {
            kind: "block",
            time: span.start_time,
            trace_id: span.trace_id,
            span_id: span.span_id,
            source: span.source,
            client,
            rule: str(attrs, Attr.GUARDRAIL_RULE),
            reason: str(attrs, Attr.GUARDRAIL_REASON) ?? span.status_message ?? null,
            redaction_kind: null,
            count: 1,
            ...NO_BUDGET,
          },
        });
      }
      for (const [i, e] of (span.events ?? []).entries()) {
        const ea = e.attributes ?? {};
        if (e.name === EventName.BUDGET_ALERT) {
          rows.push({
            time: isoMicros(e.time),
            stored,
            seq: i + 1,
            event: {
              kind: "budget",
              time: e.time,
              trace_id: span.trace_id,
              span_id: span.span_id,
              source: span.source,
              client,
              rule: null,
              reason: null,
              redaction_kind: null,
              count: 1,
              budget_limit_usd: finite(ea, Attr.BUDGET_LIMIT_USD),
              budget_spent_usd: finite(ea, Attr.BUDGET_SPENT_USD),
              budget_scope: str(ea, Attr.BUDGET_SCOPE)?.slice(0, 20) ?? null,
            },
          });
          continue;
        }
        if (e.name !== EventName.REDACTION) continue;
        rows.push({
          time: isoMicros(e.time),
          stored,
          seq: i + 1,
          event: {
            kind: "redaction",
            time: e.time,
            trace_id: span.trace_id,
            span_id: span.span_id,
            source: span.source,
            client,
            rule: null,
            reason: null,
            redaction_kind: (str(ea, Attr.REDACTION_KIND) ?? "unknown").slice(0, 100),
            count: redactionCount(ea[Attr.REDACTION_COUNT]),
            ...NO_BUDGET,
          },
        });
      }
    }
  }
  const desc = (a: string, b: string) => compareCodePoints(b, a);
  return rows.sort(
    (a, b) =>
      b.time - a.time ||
      desc(a.event.trace_id, b.event.trace_id) ||
      desc(a.event.span_id, b.event.span_id) ||
      b.seq - a.seq,
  );
}

interface CostRow {
  start: number; // epoch ms
  model: string;
  client: string;
  service: string;
  cost: number; // COST_UNITS
  input: number;
  output: number;
}

const pick = (r: CostRow, group: CostGroup): string =>
  group === "model" ? r.model : group === "client" ? r.client : r.service;

/** Every span with a cost, as the cost series sees it (`spans.cost_usd IS NOT NULL`). */
function costRows(snapshot: DemoSnapshot): CostRow[] {
  const rows: CostRow[] = [];
  for (const list of Object.values(snapshot.spans)) {
    for (const span of list) {
      const attrs = span.attributes ?? {};
      const cost = finite(attrs, Attr.COST_USD);
      if (cost === null) continue;
      const model =
        [Attr.GEN_AI_RESPONSE_MODEL, Attr.GEN_AI_REQUEST_MODEL]
          .map((k) => str(attrs, k))
          .find((v) => v !== null && v !== "") ?? "other";
      rows.push({
        start: Date.parse(span.start_time),
        model,
        client: str(attrs, Attr.CLIENT) ?? "other",
        service: str(attrs, Attr.SERVICE_NAME) ?? "other",
        cost: Math.round(cost * COST_UNITS),
        input: int(attrs, Attr.GEN_AI_INPUT_TOKENS) ?? 0,
        output: int(attrs, Attr.GEN_AI_OUTPUT_TOKENS) ?? 0,
      });
    }
  }
  return rows;
}

/** The server's bucket size for a window (`bucket_seconds_for`): 5 min up to 2 h, 1 h up to
 * 2 days, else 1 day. */
export function bucketSeconds(windowMs: number): number {
  if (windowMs <= 2 * 3600_000) return 300;
  if (windowMs <= 2 * 86_400_000) return 3600;
  return 86400;
}

/** An eval run as its list row. */
export function toEvalSummary(run: EvalRun): EvalRunSummary {
  const passed = run.cases.filter((c) => c.passed).length;
  return {
    id: run.id,
    suite: run.suite,
    status: run.status,
    started_at: run.started_at,
    passed,
    failed: run.cases.length - passed,
    regressions: run.cases.filter((c) => c.baseline_passed === true && !c.passed).length,
    cost_usd: run.cost_usd,
    baseline_cost_usd: run.baseline_cost_usd,
    git_sha: run.git_sha ?? null,
    git_ref: run.git_ref ?? null,
    ci_url: run.ci_url ?? null,
  };
}

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

  // ------------------------------------------------------------------ M3

  const guardrails = guardrailRows(snapshot);

  const parseLimit = (params: URLSearchParams): number => {
    const rawLimit = params.get("limit");
    const limit = rawLimit === null ? DEFAULT_LIMIT : Number(rawLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
      throw new InvalidParam("limit", `limit must be an integer from 1 to ${MAX_LIMIT}`);
    }
    return limit;
  };

  const eventInWindow = (row: EventRow, from: number | null, to: number | null) =>
    (from === null || row.time >= from * 1000) && (to === null || row.time < to * 1000);

  function guardrailEvents(params: URLSearchParams): Response {
    const limit = parseLimit(params);
    const from = parseTime(params, "from");
    const to = parseTime(params, "to");
    const since = parseTime(params, "since");
    const kinds = [...new Set(params.getAll("kind"))].sort();
    if (!kinds.every((k) => (GUARDRAIL_EVENT_KINDS as readonly string[]).includes(k))) {
      return invalid("kind", `kind must be one of ${GUARDRAIL_EVENT_KINDS.join(", ")}`);
    }
    const rawCursor = params.get("cursor");
    if (since !== null && rawCursor !== null) {
      return invalid("cursor", "cursor cannot be combined with since");
    }
    // Every kind is the same query as none (and the same cursor), like the API.
    const wanted = kinds.length < GUARDRAIL_EVENT_KINDS.length ? kinds : [];
    const fp = fingerprintOf([from, to, wanted]);
    let after = -1;
    if (rawCursor !== null) {
      const match = /^e([0-9]+)\.([0-9a-z]+)$/.exec(rawCursor);
      if (match?.[2] !== fp) return invalid("cursor", "invalid cursor");
      after = Number(match[1]);
    }
    const page: GuardrailEvent[] = [];
    let lastIndex = -1;
    let hasNext = false;
    for (let i = after + 1; i < guardrails.length; i++) {
      const row = guardrails[i];
      if (!row) break;
      if (!eventInWindow(row, from, to)) continue;
      if (wanted.length && !wanted.includes(row.event.kind)) continue;
      // Live polling: the snapshot never changes, so "stored after `since`" means its span
      // ended after it.
      if (since !== null && row.stored <= since * 1000) continue;
      if (page.length === limit) {
        hasNext = true;
        break;
      }
      page.push(row.event);
      lastIndex = i;
    }
    const body: GuardrailEventList = {
      events: page,
      next_cursor: hasNext && since === null ? `e${lastIndex}.${fp}` : null,
      as_of: new Date().toISOString(),
    };
    return json(body);
  }

  function guardrailSummary(params: URLSearchParams): Response {
    const from = parseTime(params, "from");
    const to = parseTime(params, "to");
    const blocks = new Map<string, number>();
    const redactions = new Map<string, number>();
    let budgetAlerts = 0;
    for (const row of guardrails) {
      if (!eventInWindow(row, from, to)) continue;
      const e = row.event;
      if (e.kind === "block") {
        const rule = e.rule ?? "unknown";
        blocks.set(rule, (blocks.get(rule) ?? 0) + 1);
      } else if (e.kind === "budget") {
        budgetAlerts += 1;
      } else {
        const kind = e.redaction_kind ?? "unknown";
        redactions.set(kind, (redactions.get(kind) ?? 0) + e.count);
      }
    }
    const body: GuardrailSummary = {
      blocks: [...blocks]
        .map(([rule, n]) => ({ rule, blocks: n }))
        .sort((a, b) => b.blocks - a.blocks || compareCodePoints(a.rule, b.rule)),
      redactions: [...redactions].map(([value, count]) => ({ value, count })).sort(compareValues),
      budget_alerts: budgetAlerts,
      as_of: new Date().toISOString(),
    };
    return json(body);
  }

  const priced = costRows(snapshot);

  function costs(params: URLSearchParams): Response {
    const from = parseTime(params, "from");
    const to = parseTime(params, "to");
    const rawGroup = params.get("group_by") ?? "model";
    const group = COST_GROUPS.find((g) => g === rawGroup);
    if (group === undefined)
      return invalid("group_by", "group_by must be model, client or service");
    // Internal (live demo only): where "now" is in snapshot time, and the shift to align
    // buckets to, so shifted buckets still start on round times.
    const now = Number(params.get("demo_now") ?? Date.parse(snapshot.generated_at));
    const shift = Number(params.get("demo_shift") ?? 0);
    const inRange = priced.filter(
      (r) => (from === null || r.start >= from) && (to === null || r.start < to),
    );
    // An open start is the oldest priced span in range; an open end is "now".
    const oldest = inRange.reduce((m, r) => Math.min(m, r.start), Infinity);
    const start = from ?? (Number.isFinite(oldest) ? oldest : null);
    const end = to ?? now;
    const bucket = bucketSeconds(start === null ? 0 : end - start);
    const bucketMs = bucket * 1000;
    const acc = new Map<string, CostPoint & { units: number }>();
    for (const r of inRange) {
      const b = Math.floor((r.start + shift) / bucketMs) * bucketMs - shift;
      const key = pick(r, group);
      const id = `${b}|${key}`;
      let p = acc.get(id);
      if (!p) {
        p = {
          bucket: isoSeconds(b),
          group: key,
          cost_usd: 0,
          input_tokens: 0,
          output_tokens: 0,
          calls: 0,
          units: 0,
        };
        acc.set(id, p);
      }
      p.units += r.cost;
      p.input_tokens += r.input;
      p.output_tokens += r.output;
      p.calls += 1;
    }
    const points: CostPoint[] = [...acc.values()]
      .sort(
        (a, b) =>
          Date.parse(a.bucket) - Date.parse(b.bucket) || compareCodePoints(a.group, b.group),
      )
      .map(({ units, ...p }) => ({ ...p, cost_usd: units / COST_UNITS }));
    const total = [...acc.values()].reduce((n, p) => n + p.units, 0);
    const body: CostSeries = {
      points,
      bucket_seconds: bucket,
      group_by: group,
      total_cost_usd: total / COST_UNITS,
      as_of: new Date().toISOString(),
    };
    return json(body);
  }

  const runs = [...(snapshot.eval_runs ?? [])].sort(
    (a, b) => isoMicros(b.started_at) - isoMicros(a.started_at) || compareCodePoints(b.id, a.id),
  );
  const runsById = new Map(runs.map((r) => [r.id, r]));

  function evalRuns(params: URLSearchParams): Response {
    const limit = parseLimit(params);
    const suite = params.get("suite");
    // Every demo run is sample data, so hiding sample data leaves none.
    const hideSample = parseBool(params, "hide_sample");
    const rawCursor = params.get("cursor");
    const fp = fingerprintOf([suite ?? "", hideSample]);
    let after = -1;
    if (rawCursor !== null) {
      const match = /^r([0-9]+)\.([0-9a-z]+)$/.exec(rawCursor);
      if (match?.[2] !== fp) return invalid("cursor", "invalid cursor");
      after = Number(match[1]);
    }
    const page: EvalRunSummary[] = [];
    let lastIndex = -1;
    let hasNext = false;
    for (let i = after + 1; i < runs.length; i++) {
      const run = runs[i];
      if (!run) break;
      if (hideSample || (suite !== null && run.suite !== suite)) continue;
      if (page.length === limit) {
        hasNext = true;
        break;
      }
      page.push(toEvalSummary(run));
      lastIndex = i;
    }
    const body: EvalRunList = { runs: page, next_cursor: hasNext ? `r${lastIndex}.${fp}` : null };
    return json(body);
  }

  function evalRun(runId: string): Response {
    const run = runsById.get(runId);
    return run ? json(run) : json({ detail: "eval run not found" }, 404);
  }

  return async function demoFetch(url: string): Promise<Response> {
    await Promise.resolve();
    const { pathname, searchParams } = new URL(url, "http://demo.invalid");
    try {
      if (pathname === "/healthz") return json({ status: "ok" });
      // The demo is all sample data, so the "Hide sample data" switch never shows.
      if (pathname === "/v1/data") return json({ sample_data: true, real_data: false });
      if (pathname === "/v1/traces") return list(searchParams);
      if (pathname === "/v1/traces/facets") return facets(searchParams);
      if (pathname === "/v1/gateway/turns") return gatewayTurns(searchParams);
      if (pathname === "/v1/gateway/summary") return gatewaySummary(searchParams);
      if (pathname === "/v1/pricing") return json(DEMO_PRICES);
      if (pathname === "/v1/costs") return costs(searchParams);
      if (pathname === "/v1/guardrails/rules") return json(snapshot.guardrail_rules ?? DEMO_RULES);
      if (pathname === "/v1/guardrails/events") return guardrailEvents(searchParams);
      if (pathname === "/v1/guardrails/summary") return guardrailSummary(searchParams);
      if (pathname === "/v1/evals/runs") return evalRuns(searchParams);
      const runId = /^\/v1\/evals\/runs\/([^/]+)$/.exec(pathname)?.[1];
      if (runId !== undefined) return evalRun(decodeURIComponent(runId));
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
    if (parsed.pathname === "/v1/costs") {
      // Buckets aligned in the viewer's time, and "now" for the bucket size.
      params.set("demo_shift", String(shift));
      params.set("demo_now", String(now() - shift));
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
    if (Array.isArray(body.events)) {
      const list = body as unknown as GuardrailEventList;
      return json({
        events: list.events.map((e) => ({ ...e, time: shiftTimeMicros(e.time, shift) })),
        next_cursor: list.next_cursor === null ? null : `${list.next_cursor}~${shift}`,
        as_of: asOf,
      } satisfies GuardrailEventList);
    }
    if (Array.isArray(body.points)) {
      const series = body as unknown as CostSeries;
      return json({
        ...series,
        points: series.points.map((p) => ({
          ...p,
          bucket: isoSeconds(Date.parse(p.bucket) + shift),
        })),
        as_of: asOf,
      } satisfies CostSeries);
    }
    if (Array.isArray(body.blocks) && Array.isArray(body.redactions)) {
      return json({
        ...(body as unknown as GuardrailSummary),
        as_of: asOf,
      } satisfies GuardrailSummary);
    }
    if (Array.isArray(body.runs)) {
      const list = body as unknown as EvalRunList;
      return json({
        runs: list.runs.map((r) => ({ ...r, started_at: shiftTimeMicros(r.started_at, shift) })),
        next_cursor: list.next_cursor === null ? null : `${list.next_cursor}~${shift}`,
      } satisfies EvalRunList);
    }
    if (Array.isArray(body.cases) && typeof body.started_at === "string") {
      const run = body as unknown as EvalRun;
      return json({ ...run, started_at: shiftTimeMicros(run.started_at, shift) } satisfies EvalRun);
    }
    if (body.trace !== undefined && Array.isArray(body.spans)) {
      const detail = body as unknown as TraceDetail;
      return json({
        trace: moveTrace(detail.trace, shift),
        spans: detail.spans.map((s) => moveSpan(s, shift)),
        as_of: asOf,
      } satisfies TraceDetail);
    }
    return json(body); // facets, health, pricing and rules carry no times
  };
}
