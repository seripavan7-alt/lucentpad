import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryKey,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  filterParams,
  prependsLive,
  rangeFrom,
  type FacetSelection,
  type RangeId,
  type TracesView,
} from "../features/traces/view";
import { useHideSamplePreference } from "../lib/samplePreference";
import { useNow } from "../lib/useNow";
import { usePolling } from "../lib/usePolling";
import { clientParams, type GatewayView } from "../features/gateway/view";
import { isoMicros } from "../lib/time";
import { byTimeDesc, eventKey, kindParams, type GuardrailsView } from "../features/guardrails/view";
import type { CostsView } from "../features/costs/view";
import {
  ApiError,
  getCosts,
  getDataInfo,
  getPricing,
  getEvalRun,
  getGuardrailRules,
  getGuardrailSummary,
  listEvalRuns,
  listGuardrailEvents,
  getGatewaySummary,
  getHealth,
  getTrace,
  getTraceFacets,
  listGatewayTurns,
  listTraces,
} from "./client";
import type {
  EvalRunList,
  GatewayTurn,
  GatewayTurnList,
  GuardrailEventList,
  Span,
  TraceDetail,
  TraceList,
  TraceSummary,
} from "./types";

export const PAGE_SIZE = 50;

/** Live polling intervals (decision D3). */
export const LIST_POLL_MS = 3_000;
export const TRACE_POLL_MS = 1_000;
/** Detail polling while a trace without a root span has gone idle (see `useLiveTrace`). */
export const TRACE_IDLE_POLL_MS = 5_000;
/** A trace with no new spans for this long is no longer "Live". */
export const TRACE_IDLE_MS = 10_000;
/** How long a trace that arrived by live polling stays highlighted in the list. */
export const FRESH_HIGHLIGHT_MS = 2_500;
/** The live list asks for at most this many changed traces per poll; more means refetch. */
const LIVE_LIMIT = 200;

/** A list page plus the window start it was fetched with (cursors are bound to it). */
export interface TracePage extends TraceList {
  from: string;
}

interface PageParam {
  cursor: string;
  from: string;
}

/** What the database holds (sample, real, both). Polled, so the "Hide sample data" toggle
 * appears once the first real trace arrives. */
export function useDataInfo() {
  return useQuery({
    queryKey: ["data-info"],
    queryFn: ({ signal }) => getDataInfo(signal),
    refetchInterval: 15_000,
  });
}

/** Whether to leave sample data out: the viewer asked to, and there is real data to show. */
export function useHideSample(): boolean {
  const pref = useHideSamplePreference();
  const info = useDataInfo();
  return pref && info.data?.real_data === true && info.data.sample_data;
}

const sampleParam = (hide: boolean): { hide_sample?: boolean } =>
  hide ? { hide_sample: true } : {};

export function tracesKey(view: TracesView, hideSample = false): QueryKey {
  return ["traces", view.range, view.sort, view.order, filterParams(view.filters), hideSample];
}

/** `sort` as a request param: left out for the API's default ("started"). */
function sortParam(view: TracesView): { sort?: TracesView["sort"] } {
  return view.sort === "started" ? {} : { sort: view.sort };
}

export function useTraces(view: TracesView) {
  const hideSample = useHideSample();
  return useInfiniteQuery({
    queryKey: tracesKey(view, hideSample),
    queryFn: async ({ pageParam, signal }): Promise<TracePage> => {
      // The first page recomputes the rolling window; later pages reuse it, because the
      // server binds each cursor to the exact filter set (`from` included).
      const from = pageParam?.from ?? rangeFrom(view.range);
      const list = await listTraces(
        {
          limit: PAGE_SIZE,
          cursor: pageParam?.cursor,
          ...sortParam(view),
          order: view.order,
          from,
          ...filterParams(view.filters),
          ...sampleParam(hideSample),
        },
        signal,
      );
      return { ...list, from };
    },
    initialPageParam: null as PageParam | null,
    getNextPageParam: (last): PageParam | null =>
      last.next_cursor ? { cursor: last.next_cursor, from: last.from } : null,
    // Keep showing the old rows while a new sort/filter loads, so the table (and the focused
    // sort header) stays in place instead of flashing to the skeleton.
    placeholderData: keepPreviousData,
  });
}

export function facetsKey(range: RangeId, filters: FacetSelection, hideSample = false): QueryKey {
  return ["facets", range, filterParams(filters), hideSample];
}

export function useTraceFacets(view: TracesView) {
  const hideSample = useHideSample();
  return useQuery({
    queryKey: facetsKey(view.range, view.filters, hideSample),
    queryFn: ({ signal }) =>
      getTraceFacets(
        {
          from: rangeFrom(view.range),
          ...filterParams(view.filters),
          ...sampleParam(hideSample),
        },
        signal,
      ),
    placeholderData: keepPreviousData,
  });
}

/** Replace known traces in place; put unseen ones on top of the first page (newest first only). */
export function mergeLivePage(
  data: InfiniteData<TracePage>,
  update: TraceList,
  prepend: boolean,
): { data: InfiniteData<TracePage>; added: string[] } {
  const incoming = new Map(update.traces.map((t) => [t.trace_id, t]));
  const known = new Set<string>();
  const pages = data.pages.map((page) => ({
    ...page,
    traces: page.traces.map((t) => {
      known.add(t.trace_id);
      return incoming.get(t.trace_id) ?? t;
    }),
  }));
  const fresh: TraceSummary[] = prepend ? update.traces.filter((t) => !known.has(t.trace_id)) : [];
  const [first, ...rest] = pages;
  if (!first) return { data, added: [] };
  return {
    data: {
      ...data,
      pages: [{ ...first, traces: [...fresh, ...first.traces], as_of: update.as_of }, ...rest],
    },
    added: fresh.map((t) => t.trace_id),
  };
}

/**
 * Ids that just arrived by live polling, each kept for FRESH_HIGHLIGHT_MS; `highlight` adds some.
 */
function useFreshIds(): [ReadonlySet<string>, (ids: string[]) => void] {
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const t of pending) clearTimeout(t);
    };
  }, []);

  const highlight = useCallback((ids: string[]) => {
    if (ids.length === 0) return;
    setFresh((prev) => new Set([...prev, ...ids]));
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setFresh((prev) => new Set([...prev].filter((id) => !ids.includes(id))));
    }, FRESH_HIGHLIGHT_MS);
    timers.current.add(timer);
  }, []);

  return [fresh, highlight];
}

/**
 * Live updates for the traces list: every LIST_POLL_MS, ask for traces that gained spans
 * since the previous response's `as_of`, update loaded rows in place and (sorted by Started,
 * newest first only) prepend new ones; other sorts don't re-sort rows as they change. Loaded pages, filters and scroll are kept. Returns the ids to
 * highlight.
 */
export function useLiveTraces(view: TracesView, enabled: boolean): ReadonlySet<string> {
  const client = useQueryClient();
  const [fresh, highlight] = useFreshIds();
  const hideSample = useHideSample();

  usePolling(
    async (signal) => {
      const key = tracesKey(view, hideSample);
      const data = client.getQueryData<InfiniteData<TracePage>>(key);
      const first = data?.pages[0];
      if (!first || client.isFetching({ queryKey: key }) > 0) return;
      const update = await listTraces(
        {
          limit: LIVE_LIMIT,
          ...sortParam(view),
          order: view.order,
          since: first.as_of,
          from: rangeFrom(view.range),
          ...filterParams(view.filters),
          ...sampleParam(hideSample),
        },
        signal,
      );
      if (update.next_cursor !== null) {
        // Too much changed to merge; start over from the first page.
        await client.invalidateQueries({ queryKey: key });
        return;
      }
      const current = client.getQueryData<InfiniteData<TracePage>>(key);
      if (!current) return;
      const merged = mergeLivePage(current, update, prependsLive(view));
      client.setQueryData(key, merged.data);
      if (merged.added.length > 0) {
        highlight(merged.added);
        void client.invalidateQueries({ queryKey: ["facets"] });
      }
    },
    { intervalMs: LIST_POLL_MS, enabled },
  );

  return fresh;
}

export function traceKey(traceId: string): QueryKey {
  return ["trace", traceId];
}

export function useTrace(traceId: string) {
  return useQuery({
    queryKey: traceKey(traceId),
    queryFn: ({ signal }) => getTrace(traceId, {}, signal),
  });
}

/** Merge polled spans into the known ones by span_id. `changed` is false when nothing differs. */
export function mergeSpans(
  current: readonly Span[],
  incoming: readonly Span[],
): { spans: Span[]; changed: boolean } {
  const byId = new Map(current.map((s) => [s.span_id, s]));
  let changed = false;
  for (const span of incoming) {
    const prev = byId.get(span.span_id);
    if (!prev || JSON.stringify(prev) !== JSON.stringify(span)) {
      byId.set(span.span_id, span);
      changed = true;
    }
  }
  return { spans: changed ? [...byId.values()] : [...current], changed };
}

export function hasRootSpan(spans: readonly Span[]): boolean {
  return spans.some((s) => !s.parent_span_id);
}

export interface LiveTraceState {
  /** Still polling fast and growing the axis: see the idle rule below. */
  live: boolean;
  /** Polling at all (fast while live, slower while idle without a root span). */
  polling: boolean;
  /** Epoch ms, ticking every TRACE_POLL_MS while polling (the live axis end). */
  now: number;
}

/**
 * Live updates for one trace (decision D3): poll `GET /v1/traces/{id}?since=<as_of>` and merge
 * the returned spans by span_id.
 *
 * Idle rule: a trace is **live** while its root span (no parent) hasn't arrived *and* a new or
 * changed span arrived in the last TRACE_IDLE_MS (10 s). SDKs export a span when it ends, so the
 * root span arriving means the run finished: polling stops. With no root but no new spans for
 * 10 s the "Live" indicator switches off and polling slows to TRACE_IDLE_POLL_MS, so a trace that
 * resumes (e.g. after a long LLM call) goes live again.
 */
export function useLiveTrace(traceId: string): LiveTraceState {
  const client = useQueryClient();
  const query = useTrace(traceId);
  const detail = query.data;
  const sinceRef = useRef<{ traceId: string; asOf: string } | null>(null);

  const rootPresent = detail ? hasRootSpan(detail.spans) : false;
  const polling = query.isSuccess && !rootPresent;
  const now = useNow(TRACE_POLL_MS, polling);
  // The cache is only written when spans change, so its update time is the last growth.
  const live = polling && now - query.dataUpdatedAt < TRACE_IDLE_MS;

  usePolling(
    async (signal) => {
      const key = traceKey(traceId);
      const cached = client.getQueryData<TraceDetail>(key);
      if (!cached) return;
      const since = sinceRef.current?.traceId === traceId ? sinceRef.current.asOf : cached.as_of;
      const update = await getTrace(traceId, { since }, signal);
      sinceRef.current = { traceId, asOf: update.as_of };
      const latest = client.getQueryData<TraceDetail>(key) ?? cached;
      const merged = mergeSpans(latest.spans, update.spans);
      if (!merged.changed) return;
      client.setQueryData<TraceDetail>(key, {
        trace: update.trace,
        spans: merged.spans,
        as_of: update.as_of,
      });
    },
    { intervalMs: live ? TRACE_POLL_MS : TRACE_IDLE_POLL_MS, enabled: polling },
  );

  return { live, polling, now };
}

// ------------------------------------------------------------------ gateway (M2)

/** A page of gateway turns plus the window start it was fetched with (cursors are bound to it). */
export interface GatewayPage extends GatewayTurnList {
  from: string;
}

export function gatewayTurnsKey(view: GatewayView, hideSample = false): QueryKey {
  return ["gateway-turns", view.range, view.client, hideSample];
}

export function gatewaySummaryKey(range: RangeId, hideSample = false): QueryKey {
  return ["gateway-summary", range, hideSample];
}

/** Gateway turns in the view's window, newest first, paged by cursor. */
export function useGatewayTurns(view: GatewayView) {
  const hideSample = useHideSample();
  return useInfiniteQuery({
    queryKey: gatewayTurnsKey(view, hideSample),
    queryFn: async ({ pageParam, signal }): Promise<GatewayPage> => {
      const from = pageParam?.from ?? rangeFrom(view.range);
      const list = await listGatewayTurns(
        {
          limit: PAGE_SIZE,
          cursor: pageParam?.cursor,
          from,
          ...clientParams(view),
          ...sampleParam(hideSample),
        },
        signal,
      );
      return { ...list, from };
    },
    initialPageParam: null as PageParam | null,
    getNextPageParam: (last): PageParam | null =>
      last.next_cursor ? { cursor: last.next_cursor, from: last.from } : null,
    placeholderData: keepPreviousData,
  });
}

/** Per-client totals for the range (every client, whatever the client filter). */
export function useGatewaySummary(range: RangeId) {
  const hideSample = useHideSample();
  return useQuery({
    queryKey: gatewaySummaryKey(range, hideSample),
    queryFn: ({ signal }) =>
      getGatewaySummary({ from: rangeFrom(range), ...sampleParam(hideSample) }, signal),
    placeholderData: keepPreviousData,
  });
}

const byStartDesc = (a: GatewayTurn, b: GatewayTurn): number =>
  isoMicros(b.start_time) - isoMicros(a.start_time) ||
  (a.span_id < b.span_id ? 1 : a.span_id > b.span_id ? -1 : 0);

/**
 * Replace known turns in place (by span_id) and put unseen ones into the first page, which is
 * kept newest first (a long streamed turn can be stored after a later, shorter one).
 */
export function mergeGatewayTurns(
  data: InfiniteData<GatewayPage>,
  update: GatewayTurnList,
): { data: InfiniteData<GatewayPage>; added: string[] } {
  const incoming = new Map(update.turns.map((t) => [t.span_id, t]));
  const known = new Set<string>();
  const pages = data.pages.map((page) => ({
    ...page,
    turns: page.turns.map((t) => {
      known.add(t.span_id);
      return incoming.get(t.span_id) ?? t;
    }),
  }));
  const fresh = update.turns.filter((t) => !known.has(t.span_id));
  const [first, ...rest] = pages;
  if (!first) return { data, added: [] };
  const turns = fresh.length ? [...fresh, ...first.turns].sort(byStartDesc) : first.turns;
  return {
    data: { ...data, pages: [{ ...first, turns, as_of: update.as_of }, ...rest] },
    added: fresh.map((t) => t.span_id),
  };
}

/**
 * Live gateway feed: every LIST_POLL_MS ask for turns stored since the previous response's
 * `as_of` (same window and client filter), merge them into the loaded pages and refresh the
 * per-client totals when something new arrived. Hidden tab → paused, errors → back off
 * (usePolling). Returns the span ids to highlight.
 */
export function useLiveGatewayTurns(view: GatewayView, enabled: boolean): ReadonlySet<string> {
  const client = useQueryClient();
  const [fresh, highlight] = useFreshIds();
  const hideSample = useHideSample();

  usePolling(
    async (signal) => {
      const key = gatewayTurnsKey(view, hideSample);
      const data = client.getQueryData<InfiniteData<GatewayPage>>(key);
      const first = data?.pages[0];
      if (!first || client.isFetching({ queryKey: key }) > 0) return;
      const update = await listGatewayTurns(
        {
          limit: LIVE_LIMIT,
          since: first.as_of,
          from: rangeFrom(view.range),
          ...clientParams(view),
          ...sampleParam(hideSample),
        },
        signal,
      );
      if (update.turns.length >= LIVE_LIMIT) {
        // Too much changed to merge (the API caps `since` at `limit`); start over.
        await client.invalidateQueries({ queryKey: key });
        void client.invalidateQueries({ queryKey: gatewaySummaryKey(view.range, hideSample) });
        return;
      }
      const current = client.getQueryData<InfiniteData<GatewayPage>>(key);
      if (!current) return;
      const merged = mergeGatewayTurns(current, update);
      client.setQueryData(key, merged.data);
      if (merged.added.length > 0) {
        highlight(merged.added);
        void client.invalidateQueries({ queryKey: gatewaySummaryKey(view.range, hideSample) });
      }
    },
    { intervalMs: LIST_POLL_MS, enabled },
  );

  return fresh;
}

// ------------------------------------------------------------------ costs (M3)

/** Retry once, but never an answer the server meant (4xx, 501 "not implemented"). */
const retryOnceIfTransient = (failures: number, error: Error): boolean =>
  failures < 1 &&
  !(
    error instanceof ApiError &&
    error.status !== 0 &&
    (error.status < 500 || error.status === 501)
  );

/** How often the Costs page refreshes its numbers while open. */
export const COSTS_REFRESH_MS = 15_000;
/** How many of the most expensive traces the Costs page lists. */
export const TOP_TRACES = 10;

export function costsKey(view: CostsView, hideSample = false): QueryKey {
  return ["costs", view.range, view.group, hideSample];
}

/** Spend per time bucket and group for the range. */
export function useCosts(view: CostsView) {
  const hideSample = useHideSample();
  return useQuery({
    queryKey: costsKey(view, hideSample),
    queryFn: ({ signal }) =>
      getCosts(
        { from: rangeFrom(view.range), group_by: view.group, ...sampleParam(hideSample) },
        signal,
      ),
    placeholderData: keepPreviousData,
    refetchInterval: COSTS_REFRESH_MS,
    retry: retryOnceIfTransient,
  });
}

/** The range's most expensive traces (the traces list sorted by cost). */
export function useTopCostTraces(range: RangeId) {
  const hideSample = useHideSample();
  return useQuery({
    queryKey: ["top-cost-traces", range, hideSample],
    queryFn: ({ signal }) =>
      listTraces(
        {
          sort: "cost",
          order: "desc",
          limit: TOP_TRACES,
          from: rangeFrom(range),
          ...sampleParam(hideSample),
        },
        signal,
      ),
    placeholderData: keepPreviousData,
    refetchInterval: COSTS_REFRESH_MS,
  });
}

/** How many budget alerts the Costs page lists (newest first). */
export const BUDGET_ALERTS_LIMIT = 20;

/** The range's budget alerts (guardrail events of kind "budget"), newest first. */
export function useBudgetAlerts(range: RangeId) {
  const hideSample = useHideSample();
  return useQuery({
    queryKey: ["budget-alerts", range, hideSample],
    queryFn: ({ signal }) =>
      listGuardrailEvents(
        {
          kind: ["budget"],
          limit: BUDGET_ALERTS_LIMIT,
          from: rangeFrom(range),
          ...sampleParam(hideSample),
        },
        signal,
      ),
    placeholderData: keepPreviousData,
    refetchInterval: COSTS_REFRESH_MS,
    retry: retryOnceIfTransient,
  });
}

/** The price table every stored span is priced with. Changes only with a server release. */
export function usePricing() {
  return useQuery({
    queryKey: ["pricing"],
    queryFn: ({ signal }) => getPricing(signal),
    staleTime: Infinity,
    retry: retryOnceIfTransient,
  });
}

// ------------------------------------------------------------------ guardrails (M3)

export function useGuardrailRules() {
  return useQuery({
    queryKey: ["guardrail-rules"],
    queryFn: ({ signal }) => getGuardrailRules(signal),
    // The server reloads its rules file; pick up edits without a page reload.
    refetchInterval: 60_000,
    retry: retryOnceIfTransient,
  });
}

export function guardrailSummaryKey(range: RangeId, hideSample = false): QueryKey {
  return ["guardrail-summary", range, hideSample];
}

/** Blocks per rule, redactions per kind and the budget alert count for the range. */
export function useGuardrailSummary(range: RangeId) {
  const hideSample = useHideSample();
  return useQuery({
    queryKey: guardrailSummaryKey(range, hideSample),
    queryFn: ({ signal }) =>
      getGuardrailSummary({ from: rangeFrom(range), ...sampleParam(hideSample) }, signal),
    placeholderData: keepPreviousData,
    retry: retryOnceIfTransient,
  });
}

/** A page of guardrail events plus the window start it was fetched with. */
export interface GuardrailPage extends GuardrailEventList {
  from: string;
}

export function guardrailEventsKey(view: GuardrailsView, hideSample = false): QueryKey {
  return ["guardrail-events", view.range, view.kind, hideSample];
}

/** Blocks, redactions and budget alerts in the range, newest first, paged by cursor. */
export function useGuardrailEvents(view: GuardrailsView) {
  const hideSample = useHideSample();
  return useInfiniteQuery({
    queryKey: guardrailEventsKey(view, hideSample),
    queryFn: async ({ pageParam, signal }): Promise<GuardrailPage> => {
      const from = pageParam?.from ?? rangeFrom(view.range);
      const list = await listGuardrailEvents(
        {
          limit: PAGE_SIZE,
          cursor: pageParam?.cursor,
          from,
          ...kindParams(view),
          ...sampleParam(hideSample),
        },
        signal,
      );
      return { ...list, from };
    },
    initialPageParam: null as PageParam | null,
    getNextPageParam: (last): PageParam | null =>
      last.next_cursor ? { cursor: last.next_cursor, from: last.from } : null,
    placeholderData: keepPreviousData,
    retry: retryOnceIfTransient,
  });
}

/** Put unseen events into the first page (kept newest first); known ones are left alone. */
export function mergeGuardrailEvents(
  data: InfiniteData<GuardrailPage>,
  update: GuardrailEventList,
): { data: InfiniteData<GuardrailPage>; added: string[] } {
  const known = new Set(data.pages.flatMap((p) => p.events.map(eventKey)));
  const fresh = update.events.filter((e) => !known.has(eventKey(e)));
  const [first, ...rest] = data.pages;
  if (!first) return { data, added: [] };
  const events = fresh.length ? [...fresh, ...first.events].sort(byTimeDesc) : first.events;
  return {
    data: { ...data, pages: [{ ...first, events, as_of: update.as_of }, ...rest] },
    added: fresh.map(eventKey),
  };
}

/**
 * Live guardrail events: every LIST_POLL_MS ask for events stored since the previous response's
 * `as_of` (same window and kind filter), prepend the new ones and refresh the counts. Returns
 * the event keys to highlight.
 */
export function useLiveGuardrailEvents(
  view: GuardrailsView,
  enabled: boolean,
): ReadonlySet<string> {
  const client = useQueryClient();
  const [fresh, highlight] = useFreshIds();
  const hideSample = useHideSample();

  usePolling(
    async (signal) => {
      const key = guardrailEventsKey(view, hideSample);
      const data = client.getQueryData<InfiniteData<GuardrailPage>>(key);
      const first = data?.pages[0];
      if (!first || client.isFetching({ queryKey: key }) > 0) return;
      const update = await listGuardrailEvents(
        {
          limit: LIVE_LIMIT,
          since: first.as_of,
          from: rangeFrom(view.range),
          ...kindParams(view),
          ...sampleParam(hideSample),
        },
        signal,
      );
      const summaryKey = guardrailSummaryKey(view.range, hideSample);
      if (update.events.length >= LIVE_LIMIT || update.next_cursor !== null) {
        // Too much changed to merge; start over.
        await client.invalidateQueries({ queryKey: key });
        void client.invalidateQueries({ queryKey: summaryKey });
        return;
      }
      const current = client.getQueryData<InfiniteData<GuardrailPage>>(key);
      if (!current) return;
      const merged = mergeGuardrailEvents(current, update);
      client.setQueryData(key, merged.data);
      if (merged.added.length > 0) {
        highlight(merged.added);
        void client.invalidateQueries({ queryKey: summaryKey });
      }
    },
    { intervalMs: LIST_POLL_MS, enabled },
  );

  return fresh;
}

// ------------------------------------------------------------------ evals (M3)

export function useEvalRuns() {
  const hideSample = useHideSample();
  return useInfiniteQuery({
    queryKey: ["eval-runs", hideSample],
    queryFn: ({ pageParam, signal }): Promise<EvalRunList> =>
      listEvalRuns(
        { limit: PAGE_SIZE, cursor: pageParam ?? undefined, ...sampleParam(hideSample) },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last): string | null => last.next_cursor,
    // New runs arrive from CI; keep the list current without live merging.
    refetchInterval: COSTS_REFRESH_MS,
    retry: retryOnceIfTransient,
  });
}

export function useEvalRun(runId: string) {
  return useQuery({
    queryKey: ["eval-run", runId],
    queryFn: ({ signal }) => getEvalRun(runId, signal),
    retry: retryOnceIfTransient,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["healthz"],
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: 15_000,
    retry: false,
  });
}
