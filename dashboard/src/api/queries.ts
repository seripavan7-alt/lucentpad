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
import { useNow } from "../lib/useNow";
import { usePolling } from "../lib/usePolling";
import { getHealth, getTrace, getTraceFacets, listTraces } from "./client";
import type { Span, TraceDetail, TraceList, TraceSummary } from "./types";

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

export function tracesKey(view: TracesView): QueryKey {
  return ["traces", view.range, view.sort, view.order, filterParams(view.filters)];
}

/** `sort` as a request param: left out for the API's default ("started"). */
function sortParam(view: TracesView): { sort?: TracesView["sort"] } {
  return view.sort === "started" ? {} : { sort: view.sort };
}

export function useTraces(view: TracesView) {
  return useInfiniteQuery({
    queryKey: tracesKey(view),
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

export function facetsKey(range: RangeId, filters: FacetSelection): QueryKey {
  return ["facets", range, filterParams(filters)];
}

export function useTraceFacets(view: TracesView) {
  return useQuery({
    queryKey: facetsKey(view.range, view.filters),
    queryFn: ({ signal }) =>
      getTraceFacets({ from: rangeFrom(view.range), ...filterParams(view.filters) }, signal),
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
 * Live updates for the traces list: every LIST_POLL_MS, ask for traces that gained spans
 * since the previous response's `as_of`, update loaded rows in place and (sorted by Started,
 * newest first only) prepend new ones; other sorts don't re-sort rows as they change. Loaded pages, filters and scroll are kept. Returns the ids to
 * highlight.
 */
export function useLiveTraces(view: TracesView, enabled: boolean): ReadonlySet<string> {
  const client = useQueryClient();
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

  usePolling(
    async (signal) => {
      const key = tracesKey(view);
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

export function useHealth() {
  return useQuery({
    queryKey: ["healthz"],
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: 15_000,
    retry: false,
  });
}
