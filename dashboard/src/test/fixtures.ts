/**
 * Hand-written fixtures typed against the generated API schema, shaped like the
 * demo in docs/PRD.md: a support agent run, a guardrail block, a Claude Code
 * session with a failover, and a failed Copilot CLI call.
 */
import type {
  Span,
  SpanEvent,
  TraceDetail,
  TraceFacets,
  TraceList,
  TraceSummary,
} from "../api/types";
import { Attr, EventName } from "../api/types";

export const BASE_TIME = Date.parse("2026-09-25T10:00:00.000Z");
export const at = (offsetMs: number): string => new Date(BASE_TIME + offsetMs).toISOString();

export const SUPPORT_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";
export const BLOCKED_TRACE_ID = "0af7651916cd43dd8448eb211c80319c";
export const CLAUDE_CODE_TRACE_ID = "5b8aa5a2d2c872e8321cf37308d69df2";
export const COPILOT_TRACE_ID = "b7ad6b7169203331e3b0a1f2c4d5e6f7";

type SpanInput = Omit<Span, "trace_id" | "source" | "start_time" | "end_time" | "status"> & {
  start: number;
  end: number;
  status?: Span["status"];
};

function spansFor(traceId: string, source: Span["source"], inputs: SpanInput[]): Span[] {
  return inputs.map(({ start, end, status = "ok", ...rest }) => ({
    trace_id: traceId,
    source,
    start_time: at(start),
    end_time: at(end),
    status,
    events: [],
    attributes: {},
    ...rest,
  }));
}

const event = (
  name: string,
  offsetMs: number,
  attributes: SpanEvent["attributes"] = {},
): SpanEvent => ({
  name,
  time: at(offsetMs),
  attributes,
});

const llm = (model: string, inputTokens: number, outputTokens: number, costUsd: number) => ({
  [Attr.GEN_AI_SYSTEM]: "anthropic",
  [Attr.GEN_AI_OPERATION]: "chat",
  [Attr.GEN_AI_REQUEST_MODEL]: model,
  [Attr.GEN_AI_RESPONSE_MODEL]: model,
  [Attr.GEN_AI_INPUT_TOKENS]: inputTokens,
  [Attr.GEN_AI_OUTPUT_TOKENS]: outputTokens,
  [Attr.COST_USD]: costUsd,
  [Attr.GEN_AI_FINISH_REASONS]: ["tool_use"],
});

const tool = (name: string) => ({
  [Attr.GEN_AI_OPERATION]: "execute_tool",
  [Attr.GEN_AI_TOOL_NAME]: name,
});

/** A captured prompt long enough to collapse in the inspector (8 lines). */
export const LONG_INPUT = [
  "System: You are a support agent for Acme.",
  "User: Where's order 1042?",
  "I want a refund.",
  "It arrived broken.",
  "The box was crushed.",
  "Please help.",
  "Thanks,",
  "Sam",
].join("\n");

// Support agent: "Where's order 1042? I want a refund." Root 0–4200ms.
export const supportSpans: Span[] = spansFor(SUPPORT_TRACE_ID, "sdk", [
  {
    span_id: "a000000000000001",
    parent_span_id: null,
    name: "support-agent",
    kind: "agent",
    start: 0,
    end: 4200,
    attributes: { [Attr.SERVICE_NAME]: "support-agent", [Attr.CLIENT]: "sdk" },
    events: [event(EventName.BUDGET_ALERT, 3900, { budget_usd: 0.05, spent_usd: 0.0512 })],
  },
  {
    span_id: "a000000000000002",
    parent_span_id: "a000000000000001",
    name: "chat claude-sonnet-4-5",
    kind: "llm",
    start: 50,
    end: 1250,
    attributes: {
      ...llm("claude-sonnet-4-5", 1840, 212, 0.0087),
      [Attr.INPUT_PREVIEW]: LONG_INPUT,
      [Attr.INPUT_TRUNCATED]: true,
      [Attr.OUTPUT_PREVIEW]: "Let me look up order 1042.",
      [Attr.OUTPUT_TRUNCATED]: false,
    },
    events: [event(EventName.REDACTION, 60, { kind: "email", count: 1 })],
  },
  {
    span_id: "a000000000000003",
    parent_span_id: "a000000000000001",
    name: "lookup_order",
    kind: "tool",
    start: 1300,
    end: 1420,
    attributes: tool("lookup_order"),
  },
  {
    span_id: "a000000000000004",
    parent_span_id: "a000000000000001",
    name: "chat claude-sonnet-4-5",
    kind: "llm",
    start: 1450,
    end: 2850,
    attributes: llm("claude-sonnet-4-5", 2260, 348, 0.0122),
  },
  {
    span_id: "a000000000000005",
    parent_span_id: "a000000000000001",
    name: "issue_refund",
    kind: "tool",
    start: 2900,
    end: 3100,
    attributes: tool("issue_refund"),
  },
  {
    span_id: "a000000000000006",
    parent_span_id: "a000000000000005",
    name: "payments.refund",
    kind: "tool",
    start: 2950,
    end: 3050,
    attributes: { ...tool("payments.refund"), "customer.email": "[REDACTED:email]" },
  },
  {
    span_id: "a000000000000007",
    parent_span_id: "a000000000000001",
    name: "draft_email",
    kind: "tool",
    start: 3150,
    end: 4150,
    attributes: tool("draft_email"),
  },
]);

export const supportSummary: TraceSummary = {
  trace_id: SUPPORT_TRACE_ID,
  name: "support-agent",
  source: "sdk",
  service_name: "support-agent",
  client: "sdk",
  start_time: at(0),
  duration_ms: 4200,
  status: "ok",
  span_count: supportSpans.length,
  llm_calls: 2,
  input_tokens: 4100,
  output_tokens: 560,
  cost_usd: 0.0209,
  models: ["claude-sonnet-4-5"],
  input_preview: "Where's order 1042?\nI want a refund.",
  output_preview: "I've issued a refund of $42.00 for order 1042.",
};

export const blockedSpans: Span[] = spansFor(BLOCKED_TRACE_ID, "sdk", [
  {
    span_id: "b000000000000001",
    parent_span_id: null,
    name: "support-agent",
    kind: "agent",
    start: 0,
    end: 980,
    status: "blocked",
    status_message: "Refund over limit",
  },
  {
    span_id: "b000000000000002",
    parent_span_id: "b000000000000001",
    name: "chat claude-sonnet-4-5",
    kind: "llm",
    start: 20,
    end: 900,
    attributes: llm("claude-sonnet-4-5", 1320, 96, 0.0054),
  },
  {
    span_id: "b000000000000003",
    parent_span_id: "b000000000000001",
    name: "refund-limit",
    kind: "guardrail",
    start: 910,
    end: 912,
    status: "blocked",
    status_message: "Refund of $900 exceeds the $200 limit",
    attributes: { [Attr.GUARDRAIL_RULE]: "refund-limit" },
    events: [event(EventName.GUARDRAIL_BLOCK, 911, { rule: "refund-limit" })],
  },
]);

export const blockedSummary: TraceSummary = {
  trace_id: BLOCKED_TRACE_ID,
  name: "support-agent",
  source: "sdk",
  service_name: "support-agent",
  client: "sdk",
  start_time: at(-5 * 60_000),
  duration_ms: 980,
  status: "blocked",
  span_count: 3,
  llm_calls: 1,
  input_tokens: 1320,
  output_tokens: 96,
  cost_usd: 0.0054,
  models: ["claude-sonnet-4-5"],
  input_preview: null,
  output_preview: null,
};

export const claudeCodeSpans: Span[] = spansFor(CLAUDE_CODE_TRACE_ID, "gateway", [
  {
    span_id: "c000000000000001",
    parent_span_id: null,
    name: "claude-code session",
    kind: "agent",
    start: 0,
    end: 95_000,
    attributes: { [Attr.CLIENT]: "claude-code", [Attr.SESSION_ID]: "sess_01" },
  },
  {
    span_id: "c000000000000002",
    parent_span_id: "c000000000000001",
    name: "POST /v1/messages",
    kind: "llm",
    start: 1000,
    end: 21_000,
    attributes: { ...llm("claude-opus-4-1", 48_200, 1_530, 0.8378), [Attr.STREAMING]: true },
  },
  {
    span_id: "c000000000000003",
    parent_span_id: "c000000000000001",
    name: "POST /v1/messages",
    kind: "llm",
    start: 30_000,
    end: 38_000,
    attributes: {
      ...llm("claude-sonnet-4-5", 51_900, 820, 0.168),
      [Attr.GEN_AI_REQUEST_MODEL]: "claude-opus-4-1",
    },
    events: [
      event(EventName.FAILOVER, 31_200, {
        from_model: "claude-opus-4-1",
        to_model: "claude-sonnet-4-5",
        reason: "529 overloaded",
      }),
    ],
  },
]);

export const claudeCodeSummary: TraceSummary = {
  trace_id: CLAUDE_CODE_TRACE_ID,
  name: "claude-code session",
  source: "gateway",
  service_name: null,
  client: "claude-code",
  start_time: at(-2 * 3600_000),
  duration_ms: 95_000,
  status: "ok",
  span_count: 3,
  llm_calls: 2,
  input_tokens: 100_100,
  output_tokens: 2_350,
  cost_usd: 1.0058,
  models: ["claude-opus-4-1", "claude-sonnet-4-5"],
  input_preview: null,
  output_preview: null,
};

export const copilotSummary: TraceSummary = {
  trace_id: COPILOT_TRACE_ID,
  name: "copilot-cli request",
  source: "gateway",
  service_name: null,
  client: "copilot-cli",
  start_time: at(-3 * 86_400_000),
  duration_ms: 312,
  status: "error",
  span_count: 1,
  llm_calls: 1,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: 0,
  models: ["gpt-4.1"],
  input_preview: null,
  output_preview: null,
};

const AS_OF = "2026-09-25T12:00:00Z";

export const traceListPage1: TraceList = {
  traces: [supportSummary, blockedSummary, claudeCodeSummary],
  next_cursor: "cursor-page-2",
  as_of: AS_OF,
};

export const traceListPage2: TraceList = {
  traces: [copilotSummary],
  next_cursor: null,
  as_of: AS_OF,
};

export const supportDetail: TraceDetail = {
  trace: supportSummary,
  spans: supportSpans,
  as_of: AS_OF,
};
export const blockedDetail: TraceDetail = {
  trace: blockedSummary,
  spans: blockedSpans,
  as_of: AS_OF,
};
export const claudeCodeDetail: TraceDetail = {
  trace: claudeCodeSummary,
  spans: claudeCodeSpans,
  as_of: AS_OF,
};

export const traceFacets: TraceFacets = {
  name: [
    { value: "support-agent", count: 12 },
    { value: "claude-code session", count: 5 },
    { value: "copilot-cli request", count: 2 },
  ],
  status: [
    { value: "ok", count: 15 },
    { value: "error", count: 3 },
    { value: "blocked", count: 1 },
  ],
  source: [
    { value: "sdk", count: 12 },
    { value: "gateway", count: 7 },
  ],
  client: [
    { value: "sdk", count: 12 },
    { value: "claude-code", count: 5 },
    { value: "copilot-cli", count: 2 },
  ],
  model: Array.from({ length: 12 }, (_, i) => ({ value: `model-${i + 1}`, count: 12 - i })),
  service: [{ value: "support-agent", count: 12 }],
};

export const emptyFacets: TraceFacets = {
  name: [],
  status: [],
  source: [],
  client: [],
  model: [],
  service: [],
};
