import type { components, operations } from "./schema";

type Schemas = components["schemas"];

export type TraceSummary = Schemas["TraceSummary"];
export type TraceList = Schemas["TraceList"];
export type TraceDetail = Schemas["TraceDetail"];
export type Span = Schemas["Span"];
export type SpanEvent = Schemas["SpanEvent"];
export type SpanKind = Span["kind"];
export type SpanStatus = Span["status"];
export type SpanSource = Span["source"];
export type AttrValue = NonNullable<Span["attributes"]>[string];

export type ListTracesParams = NonNullable<
  operations["list_traces_v1_traces_get"]["parameters"]["query"]
>;

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
} as const;

/** Event names; mirrors `EventName` in server/lucentpad_server/schema.py. */
export const EventName = {
  FAILOVER: "lucentpad.failover",
  BUDGET_ALERT: "lucentpad.budget.alert",
  REDACTION: "lucentpad.redaction",
  GUARDRAIL_BLOCK: "lucentpad.guardrail.block",
} as const;
