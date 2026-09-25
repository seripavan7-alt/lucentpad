import type { components, operations } from "./schema";

type Schemas = components["schemas"];

export type TraceSummary = Schemas["TraceSummary"];
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

export type ListTracesParams = NonNullable<
  operations["list_traces_v1_traces_get"]["parameters"]["query"]
>;
export type TraceFacetsParams = NonNullable<
  operations["trace_facets_v1_traces_facets_get"]["parameters"]["query"]
>;
export type GetTraceParams = NonNullable<
  operations["get_trace_v1_traces__trace_id__get"]["parameters"]["query"]
>;
export type TraceOrder = NonNullable<ListTracesParams["order"]>;

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
} as const;

/** Event names; mirrors `EventName` in server/lucentpad_server/schema.py. */
export const EventName = {
  FAILOVER: "lucentpad.failover",
  BUDGET_ALERT: "lucentpad.budget.alert",
  REDACTION: "lucentpad.redaction",
  GUARDRAIL_BLOCK: "lucentpad.guardrail.block",
} as const;
