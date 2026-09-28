import type {
  CostSeries,
  CostsParams,
  DataInfo,
  EvalRun,
  EvalRunList,
  EvalRunsParams,
  GatewaySummary,
  GatewaySummaryParams,
  GatewayTurnList,
  GatewayTurnsParams,
  GetTraceParams,
  GuardrailEventList,
  GuardrailEventsParams,
  GuardrailRules,
  GuardrailSummary,
  GuardrailSummaryParams,
  ListTracesParams,
  PriceTable,
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

type QueryValue = string | number | boolean | readonly string[] | null | undefined;

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

export function listGatewayTurns(
  params: GatewayTurnsParams = {},
  signal?: AbortSignal,
): Promise<GatewayTurnList> {
  return getJson<GatewayTurnList>(buildUrl("/v1/gateway/turns", params), signal);
}

export function getGatewaySummary(
  params: GatewaySummaryParams = {},
  signal?: AbortSignal,
): Promise<GatewaySummary> {
  return getJson<GatewaySummary>(buildUrl("/v1/gateway/summary", params), signal);
}

export function getCosts(params: CostsParams = {}, signal?: AbortSignal): Promise<CostSeries> {
  return getJson<CostSeries>(buildUrl("/v1/costs", params), signal);
}

export function getPricing(signal?: AbortSignal): Promise<PriceTable> {
  return getJson<PriceTable>("/v1/pricing", signal);
}

export function getGuardrailRules(signal?: AbortSignal): Promise<GuardrailRules> {
  return getJson<GuardrailRules>("/v1/guardrails/rules", signal);
}

export function listGuardrailEvents(
  params: GuardrailEventsParams = {},
  signal?: AbortSignal,
): Promise<GuardrailEventList> {
  return getJson<GuardrailEventList>(buildUrl("/v1/guardrails/events", params), signal);
}

export function getGuardrailSummary(
  params: GuardrailSummaryParams = {},
  signal?: AbortSignal,
): Promise<GuardrailSummary> {
  return getJson<GuardrailSummary>(buildUrl("/v1/guardrails/summary", params), signal);
}

export function listEvalRuns(
  params: EvalRunsParams = {},
  signal?: AbortSignal,
): Promise<EvalRunList> {
  return getJson<EvalRunList>(buildUrl("/v1/evals/runs", params), signal);
}

export function getEvalRun(runId: string, signal?: AbortSignal): Promise<EvalRun> {
  return getJson<EvalRun>(`/v1/evals/runs/${encodeURIComponent(runId)}`, signal);
}

export function getDataInfo(signal?: AbortSignal): Promise<DataInfo> {
  return getJson<DataInfo>("/v1/data", signal);
}

export function getHealth(signal?: AbortSignal): Promise<Record<string, string>> {
  return getJson<Record<string, string>>("/healthz", signal);
}
