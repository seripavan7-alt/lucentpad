import type {
  GetTraceParams,
  ListTracesParams,
  TraceDetail,
  TraceFacets,
  TraceFacetsParams,
  TraceList,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type QueryValue = string | number | readonly string[] | null | undefined;

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

let fetcher: Fetcher = (url, init) => fetch(url, init);

/** Swap how requests are sent: the static demo answers them in the browser (src/demo/adapter.ts). */
export function setFetcher(next: Fetcher): void {
  fetcher = next;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (Array.isArray(value)) {
      // Repeated params: ?status=error&status=blocked
      for (const item of value as readonly string[]) params.append(key, item);
    } else if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Non-JSON error body; fall through to the status text.
  }
  return res.statusText || `Request failed with status ${res.status}`;
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetcher(url, { headers: { Accept: "application/json" }, signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Can't reach the LucentPad API.");
  }
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  return (await res.json()) as T;
}

export function listTraces(
  params: ListTracesParams = {},
  signal?: AbortSignal,
): Promise<TraceList> {
  return getJson<TraceList>(buildUrl("/v1/traces", params), signal);
}

export function getTraceFacets(
  params: TraceFacetsParams = {},
  signal?: AbortSignal,
): Promise<TraceFacets> {
  return getJson<TraceFacets>(buildUrl("/v1/traces/facets", params), signal);
}

export function getTrace(
  traceId: string,
  params: GetTraceParams = {},
  signal?: AbortSignal,
): Promise<TraceDetail> {
  return getJson<TraceDetail>(
    buildUrl(`/v1/traces/${encodeURIComponent(traceId)}`, params),
    signal,
  );
}

export function getHealth(signal?: AbortSignal): Promise<Record<string, string>> {
  return getJson<Record<string, string>>("/healthz", signal);
}
