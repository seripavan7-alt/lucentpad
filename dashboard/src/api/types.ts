import type { components, operations } from "./schema";

type Schemas = components["schemas"];

export type TraceSummary = Schemas["TraceSummary"];
export type DataInfo = Schemas["DataInfo"];
export type TraceList = Schemas["TraceList"];
export type TraceDetail = Schemas["TraceDetail"];
export type TraceFacets = Schemas["TraceFacets"];
export type FacetValue = Schemas["FacetValue"];
export type Span = Schemas["Span"];
export type SpanEvent = Schemas["SpanEvent"];
export type SpanKind = Span["kind"];
export type SpanStatus = Span["status"];
export type SpanSource = Span["source"];
export type AttrValue = NonNullable<Span["attributes"]>[string];
export type GatewayTurn = Schemas["GatewayTurn"];
export type GatewayTurnList = Schemas["GatewayTurnList"];
export type GatewayClientTotals = Schemas["GatewayClientTotals"];
export type GatewaySummary = Schemas["GatewaySummary"];
export type GatewayProvider = NonNullable<GatewayTurn["provider"]>;
export type CostPoint = Schemas["CostPoint"];
export type CostSeries = Schemas["CostSeries"];
export type CostGroup = CostSeries["group_by"];
export type ModelPrice = Schemas["ModelPrice"];
export type PriceTable = Schemas["PriceTable"];
export type GuardrailRule = Schemas["GuardrailRule"];
export type GuardrailRules = Schemas["GuardrailRules"];
export type GuardrailEvent = Schemas["GuardrailEvent"];
export type GuardrailEventKind = GuardrailEvent["kind"];
export type GuardrailEventList = Schemas["GuardrailEventList"];
export type GuardrailSummary = Schemas["GuardrailSummary"];
export type EvalRun = Schemas["EvalRun"];
export type EvalRunSummary = Schemas["EvalRunSummary"];
export type EvalRunList = Schemas["EvalRunList"];
export type EvalStatus = EvalRun["status"];
export type EvalCaseResult = Schemas["EvalCaseResult"];
export type EvalCheckResult = Schemas["EvalCheckResult"];

export type ListTracesParams = NonNullable<
  operations["list_traces_v1_traces_get"]["parameters"]["query"]
>;
export type TraceFacetsParams = NonNullable<
  operations["trace_facets_v1_traces_facets_get"]["parameters"]["query"]
>;
export type GetTraceParams = NonNullable<
  operations["get_trace_v1_traces__trace_id__get"]["parameters"]["query"]
>;
export type GatewayTurnsParams = NonNullable<
  operations["gateway_turns_v1_gateway_turns_get"]["parameters"]["query"]
>;
export type GatewaySummaryParams = NonNullable<
  operations["gateway_summary_v1_gateway_summary_get"]["parameters"]["query"]
>;
export type CostsParams = NonNullable<operations["costs_v1_costs_get"]["parameters"]["query"]>;
export type GuardrailEventsParams = NonNullable<
  operations["guardrail_events_v1_guardrails_events_get"]["parameters"]["query"]
>;
export type GuardrailSummaryParams = NonNullable<
  operations["guardrail_summary_v1_guardrails_summary_get"]["parameters"]["query"]
>;
export type EvalRunsParams = NonNullable<
  operations["list_eval_runs_v1_evals_runs_get"]["parameters"]["query"]
>;
export type TraceOrder = NonNullable<ListTracesParams["order"]>;
export type TraceSort = NonNullable<ListTracesParams["sort"]>;

/** Every sort key; mirrors `TraceSort` in server/lucentpad_server/schema.py. */
export const TRACE_SORTS = [
  "started",
  "duration",
  "name",
  "source",
  "cost",
] as const satisfies readonly TraceSort[];

/** Facet (and filter) names in display order; mirrors `FACETS` in server/lucentpad_server/query.py. */
export const FACETS = [
  "name",
  "status",
  "source",
  "client",
  "model",
  "service",
] as const satisfies readonly (keyof TraceFacets)[];
export type Facet = (typeof FACETS)[number];

/** Server limits; mirror `schema.PREVIEW_MAX_CHARS` / `schema.FACET_MAX_VALUES`. */
export const PREVIEW_MAX_CHARS = 2000;
export const FACET_MAX_VALUES = 50;

export const SPAN_SOURCES = ["sdk", "gateway"] as const satisfies readonly SpanSource[];
export const SPAN_STATUSES = ["ok", "error", "blocked"] as const satisfies readonly SpanStatus[];

/** Attribute keys; mirrors `Attr` in server/lucentpad_server/schema.py. */
export const Attr = {
  SERVICE_NAME: "service.name",
  GEN_AI_SYSTEM: "gen_ai.system",
  GEN_AI_OPERATION: "gen_ai.operation.name",
  GEN_AI_REQUEST_MODEL: "gen_ai.request.model",
  GEN_AI_RESPONSE_MODEL: "gen_ai.response.model",
  GEN_AI_INPUT_TOKENS: "gen_ai.usage.input_tokens",
  GEN_AI_OUTPUT_TOKENS: "gen_ai.usage.output_tokens",
  GEN_AI_FINISH_REASONS: "gen_ai.response.finish_reasons",
  GEN_AI_TOOL_NAME: "gen_ai.tool.name",
  COST_USD: "lucentpad.cost_usd",
  STREAMING: "lucentpad.streaming",
  CLIENT: "lucentpad.client",
  SESSION_ID: "lucentpad.session_id",
  GUARDRAIL_RULE: "lucentpad.guardrail.rule",
  // Prompt/response previews (<= PREVIEW_MAX_CHARS); *_TRUNCATED is true when cut at capture.
  INPUT_PREVIEW: "lucentpad.input.preview",
  OUTPUT_PREVIEW: "lucentpad.output.preview",
  INPUT_TRUNCATED: "lucentpad.input.truncated",
  OUTPUT_TRUNCATED: "lucentpad.output.truncated",
  // Failover event attributes
  FAILOVER_FROM_MODEL: "lucentpad.failover.from_model",
  FAILOVER_TO_MODEL: "lucentpad.failover.to_model",
  FAILOVER_STATUS_CODE: "lucentpad.failover.status_code",
  FAILOVER_RETRIES: "lucentpad.failover.retries",
  // Budget alert event attributes
  BUDGET_LIMIT_USD: "lucentpad.budget.limit_usd",
  BUDGET_SPENT_USD: "lucentpad.budget.spent_usd",
  // Redaction event attributes
  REDACTION_KIND: "lucentpad.redaction.kind",
  REDACTION_COUNT: "lucentpad.redaction.count",
  // Demo support agent
  REFUND_AMOUNT: "lucentpad.refund.amount",
  // Gateway (M2)
  GATEWAY_UPSTREAM: "lucentpad.gateway.upstream",
  TTFB_MS: "lucentpad.ttfb_ms",
  KEY_FINGERPRINT: "lucentpad.key_fingerprint",
  // Guardrails, budgets, evals (M3)
  GUARDRAIL_REASON: "lucentpad.guardrail.reason",
  BUDGET_SCOPE: "lucentpad.budget.scope",
  EVAL_RUN_ID: "lucentpad.eval.run_id",
  EVAL_CASE: "lucentpad.eval.case",
} as const;

/** Cost series groupings; mirrors `CostGroup` in schema.py. */
export const COST_GROUPS = ["model", "client", "service"] as const satisfies readonly CostGroup[];

/** Guardrail event kinds; mirrors `GuardrailEventKind` in schema.py. */
export const GUARDRAIL_EVENT_KINDS = [
  "block",
  "redaction",
  "budget",
] as const satisfies readonly GuardrailEventKind[];

/** Eval run statuses; mirrors `EvalStatus` in schema.py. */
export const EVAL_STATUSES = [
  "passed",
  "failed",
  "regressed",
  "error",
] as const satisfies readonly EvalStatus[];

/** The clients the gateway recognises (`GatewayTurn.client`), in display order. */
export const GATEWAY_CLIENTS = ["claude-code", "copilot-chat", "copilot-cli", "other"] as const;
export type GatewayClient = (typeof GATEWAY_CLIENTS)[number];

/** Upstreams the gateway forwards to; mirrors `GatewayProvider` in schema.py. */
export const GATEWAY_PROVIDERS = [
  "anthropic",
  "openai",
] as const satisfies readonly GatewayProvider[];

/** Event names; mirrors `EventName` in server/lucentpad_server/schema.py. */
export const EventName = {
  FAILOVER: "lucentpad.failover",
  BUDGET_ALERT: "lucentpad.budget.alert",
  REDACTION: "lucentpad.redaction",
  GUARDRAIL_BLOCK: "lucentpad.guardrail.block",
} as const;
