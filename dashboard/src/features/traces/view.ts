/*
 * The Traces page's view state, kept in the URL so every view is linkable:
 *   ?range=1h            time-range preset (default 24h, omitted from the URL)
 *   ?sort=cost           sort key: started (default, omitted), duration, name, source, cost
 *   ?order=asc           direction (default desc, omitted)
 *   ?name=a&name=b&status=error&source=sdk&client=…&model=…&service=…
 *                        facet filters, repeated for OR (AND across facets)
 * Paging is not in the URL: any change here starts again from the first page.
 */
import { useSearchParams } from "react-router";
import {
  FACETS,
  SPAN_SOURCES,
  SPAN_STATUSES,
  type Facet,
  type FacetValue,
  type ListTracesParams,
  TRACE_SORTS,
  type TraceOrder,
  type TraceSort,
} from "../../api/types";
import { clientLabel } from "../../lib/format";
import { statusLabel } from "../../lib/labels";

export const RANGES = [
  { id: "15m", ms: 15 * 60_000, phrase: "15 minutes" },
  { id: "1h", ms: 3600_000, phrase: "hour" },
  { id: "4h", ms: 4 * 3600_000, phrase: "4 hours" },
  { id: "24h", ms: 24 * 3600_000, phrase: "24 hours" },
  { id: "7d", ms: 7 * 86_400_000, phrase: "7 days" },
  { id: "30d", ms: 30 * 86_400_000, phrase: "30 days" },
] as const;

export type RangeId = (typeof RANGES)[number]["id"];
export const DEFAULT_RANGE: RangeId = "24h";
export const DEFAULT_ORDER: TraceOrder = "desc";
export const DEFAULT_SORT: TraceSort = "started";

/** The direction a column sorts in when first clicked: biggest/newest first, text A→Z. */
export const FIRST_ORDER: Record<TraceSort, TraceOrder> = {
  started: "desc",
  duration: "desc",
  cost: "desc",
  name: "asc",
  source: "asc",
};

/** Clicking a sortable header: flip the active column, or switch to another in its first direction. */
export function nextSort(
  view: Pick<TracesView, "sort" | "order">,
  column: TraceSort,
): { sort: TraceSort; order: TraceOrder } {
  if (view.sort === column) return { sort: column, order: view.order === "desc" ? "asc" : "desc" };
  return { sort: column, order: FIRST_ORDER[column] };
}

/** Live polling may put new traces on top only when the list is newest first. */
export function prependsLive(view: Pick<TracesView, "sort" | "order">): boolean {
  return view.sort === "started" && view.order === "desc";
}

export type FacetSelection = Record<Facet, readonly string[]>;

export interface TracesView {
  range: RangeId;
  sort: TraceSort;
  order: TraceOrder;
  filters: FacetSelection;
}

export function rangeInfo(id: RangeId): (typeof RANGES)[number] {
  return RANGES.find((r) => r.id === id) ?? RANGES[0];
}

/** "No traces in the last 15 minutes" / "…in the last hour". */
export function rangePhrase(id: RangeId): string {
  return `the last ${rangeInfo(id).phrase}`;
}

/** Rolling window start: recomputed on every fetch, so "last 15 minutes" stays true. */
export function rangeFrom(id: RangeId, now: number = Date.now()): string {
  return new Date(now - rangeInfo(id).ms).toISOString();
}

export const FACET_LABELS: Record<Facet, string> = {
  name: "Name",
  status: "Status",
  source: "Source",
  client: "Client",
  model: "Model",
  service: "Service",
};

/** How a facet value reads in the panel ("error" → "Error", "claude-code" → "Claude Code"). */
export function facetValueLabel(facet: Facet, value: string): string {
  if (facet === "status" && (SPAN_STATUSES as readonly string[]).includes(value)) {
    return statusLabel(value as (typeof SPAN_STATUSES)[number]);
  }
  if (facet === "source") return value === "sdk" ? "SDK" : value === "gateway" ? "Gateway" : value;
  if (facet === "client") return clientLabel(value) ?? value;
  return value;
}

const ENUM_FACETS: Partial<Record<Facet, readonly string[]>> = {
  status: SPAN_STATUSES,
  source: SPAN_SOURCES,
};

function unique(values: readonly string[]): string[] {
  return [...new Set(values)];
}

export function parseView(params: URLSearchParams): TracesView {
  const rawRange = params.get("range");
  const range = RANGES.find((r) => r.id === rawRange)?.id ?? DEFAULT_RANGE;
  const rawSort = params.get("sort");
  const sort = TRACE_SORTS.find((s) => s === rawSort) ?? DEFAULT_SORT;
  const order: TraceOrder = params.get("order") === "asc" ? "asc" : "desc";
  const filters = {} as Record<Facet, readonly string[]>;
  for (const facet of FACETS) {
    const allowed = ENUM_FACETS[facet];
    filters[facet] = unique(
      params.getAll(facet).filter((v) => v !== "" && (!allowed || allowed.includes(v))),
    );
  }
  return { range, sort, order, filters };
}

export function activeFilterCount(filters: FacetSelection): number {
  return FACETS.reduce((n, f) => n + filters[f].length, 0);
}

/** The filter part of a list/facets query: only facets with a selection, as repeated params. */
export function filterParams(
  filters: FacetSelection,
): Pick<ListTracesParams, "name" | "status" | "source" | "client" | "model" | "service"> {
  const out: Record<string, string[]> = {};
  for (const facet of FACETS) {
    if (filters[facet].length) out[facet] = [...filters[facet]].sort();
  }
  return out;
}

export interface ViewPatch {
  range?: RangeId;
  sort?: TraceSort;
  order?: TraceOrder;
  filters?: Partial<FacetSelection>;
}

export function applyPatch(prev: URLSearchParams, patch: ViewPatch): URLSearchParams {
  const next = new URLSearchParams(prev);
  if (patch.range !== undefined) {
    if (patch.range === DEFAULT_RANGE) next.delete("range");
    else next.set("range", patch.range);
  }
  if (patch.sort !== undefined) {
    if (patch.sort === DEFAULT_SORT) next.delete("sort");
    else next.set("sort", patch.sort);
  }
  if (patch.order !== undefined) {
    if (patch.order === DEFAULT_ORDER) next.delete("order");
    else next.set("order", patch.order);
  }
  for (const [facet, values] of Object.entries(patch.filters ?? {})) {
    next.delete(facet);
    for (const v of unique(values)) next.append(facet, v);
  }
  return next;
}

export function useTracesView(): [TracesView, (patch: ViewPatch) => void] {
  const [params, setParams] = useSearchParams();
  const view = parseView(params);
  const update = (patch: ViewPatch) => {
    setParams((prev) => applyPatch(prev, patch));
  };
  return [view, update];
}

export const NO_FILTERS: FacetSelection = Object.fromEntries(
  FACETS.map((f) => [f, [] as readonly string[]]),
) as unknown as FacetSelection;

/** Server values plus any selected value it didn't return (shown at count 0). */
export function facetRows(
  values: readonly FacetValue[] | undefined,
  selected: readonly string[],
): FacetValue[] {
  const rows = [...(values ?? [])];
  const seen = new Set(rows.map((r) => r.value));
  for (const value of selected) if (!seen.has(value)) rows.push({ value, count: 0 });
  return rows.sort(
    (a, b) => b.count - a.count || (a.value < b.value ? -1 : a.value > b.value ? 1 : 0),
  );
}
