import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { getHealth, getTrace, listTraces } from "./client";
import type { SpanSource, SpanStatus } from "./types";

export const PAGE_SIZE = 50;

export interface TraceFilters {
  source?: SpanSource | undefined;
  status?: SpanStatus | undefined;
}

export function useTraces(filters: TraceFilters) {
  return useInfiniteQuery({
    queryKey: ["traces", filters.source ?? null, filters.status ?? null],
    queryFn: ({ pageParam, signal }) =>
      listTraces(
        { limit: PAGE_SIZE, cursor: pageParam, source: filters.source, status: filters.status },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

export function useTrace(traceId: string) {
  return useQuery({
    queryKey: ["trace", traceId],
    queryFn: ({ signal }) => getTrace(traceId, signal),
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
