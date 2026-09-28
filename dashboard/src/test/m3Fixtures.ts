/** Hand-written fixtures for the Costs, Guardrails and Evals pages (M3), typed against the API. */
import type {
  CostPoint,
  CostSeries,
  EvalRun,
  EvalRunList,
  GuardrailEvent,
  GuardrailEventList,
  GuardrailRules,
  GuardrailSummary,
  PriceTable,
} from "../api/types";
import { at, BASE_TIME, BLOCKED_TRACE_ID, SUPPORT_TRACE_ID } from "./fixtures";

const HOUR = 3600_000;
/** Pages under test run at this time (10 minutes after the base time). */
export const M3_NOW = BASE_TIME + 10 * 60_000;
const AS_OF = new Date(M3_NOW).toISOString();

const point = (
  hoursAgo: number,
  group: string,
  cost: number,
  calls = 1,
  tokens: [number, number] = [1000, 100],
): CostPoint => ({
  bucket: new Date(BASE_TIME - hoursAgo * HOUR).toISOString(),
  group,
  cost_usd: cost,
  calls,
  input_tokens: tokens[0],
  output_tokens: tokens[1],
});

/** 24 h by model, 1-hour buckets: sonnet $1.50 (3 buckets), haiku $0.25, unknown $0.05. */
export const costsByModel: CostSeries = {
  group_by: "model",
  bucket_seconds: 3600,
  total_cost_usd: 1.8,
  as_of: AS_OF,
  points: [
    point(5, "claude-sonnet-5", 0.5, 4, [40_000, 2_000]),
    point(5, "claude-haiku-4-5", 0.25, 5, [60_000, 3_000]),
    point(2, "claude-sonnet-5", 0.75, 6, [70_000, 3_500]),
    point(0, "claude-sonnet-5", 0.25, 2, [20_000, 1_000]),
    point(0, "other", 0.05, 1, [5_000, 500]),
  ],
};

export const costsByClient: CostSeries = {
  group_by: "client",
  bucket_seconds: 3600,
  total_cost_usd: 1.2,
  as_of: AS_OF,
  points: [point(3, "claude-code", 1.0, 10), point(1, "sdk", 0.2, 3)],
};

export const emptyCosts: CostSeries = {
  group_by: "model",
  bucket_seconds: 3600,
  total_cost_usd: 0,
  as_of: AS_OF,
  points: [],
};

export const priceTable: PriceTable = {
  checked: "2026-09-25",
  prices: [
    { model: "claude-haiku-4-5", input: 1, output: 5, cache_read: 0.1, cache_write: 1.25 },
    { model: "claude-sonnet-5", input: 2, output: 10, cache_read: 0.2, cache_write: 2.5 },
  ],
};

// ------------------------------------------------------------------ guardrails

export const rules: GuardrailRules = {
  source: "/etc/lucentpad/rules.yaml",
  version: "3f2a9c1be04d77aa",
  rules: [
    {
      id: "refund_limit",
      type: "tool",
      description: "Refunds over $200 need a person.",
      keywords: [],
      pattern: null,
      tool: "issue_refund",
      condition: "amount > 200",
      message: "Refunds over $200 need a human agent.",
    },
    {
      id: "no_legal_advice",
      type: "prompt",
      description: null,
      keywords: ["lawsuit", "sue"],
      pattern: null,
      tool: null,
      condition: null,
      message: "We can't give legal advice.",
    },
  ],
};

export const guardrailSummary: GuardrailSummary = {
  as_of: AS_OF,
  blocks: [
    { rule: "refund_limit", blocks: 3 },
    { rule: "old_rule", blocks: 1 },
  ],
  redactions: [
    { value: "email", count: 12 },
    { value: "api_key", count: 2 },
  ],
  budget_alerts: 2,
};

const SPAN = (n: number) => n.toString(16).padStart(16, "0");

export const blockEvent = (minutesAgo: number, spanN: number): GuardrailEvent => ({
  kind: "block",
  time: at(-minutesAgo * 60_000),
  trace_id: BLOCKED_TRACE_ID,
  span_id: SPAN(spanN),
  source: "sdk",
  client: "sdk",
  rule: "refund_limit",
  reason: "refund of $489 exceeds the $200 limit",
  redaction_kind: null,
  count: 1,
});

export const redactionEvent = (minutesAgo: number, spanN: number, count = 1): GuardrailEvent => ({
  kind: "redaction",
  time: at(-minutesAgo * 60_000),
  trace_id: SUPPORT_TRACE_ID,
  span_id: SPAN(spanN),
  source: "gateway",
  client: "claude-code",
  rule: null,
  reason: null,
  redaction_kind: "email",
  count,
});

export const budgetEvent = (
  minutesAgo: number,
  spanN: number,
  over: Partial<GuardrailEvent> = {},
): GuardrailEvent => ({
  kind: "budget",
  time: at(-minutesAgo * 60_000),
  trace_id: SUPPORT_TRACE_ID,
  span_id: SPAN(spanN),
  source: "sdk",
  client: "sdk",
  rule: null,
  reason: null,
  redaction_kind: null,
  count: 1,
  budget_limit_usd: 0.5,
  budget_spent_usd: 0.62,
  budget_scope: "run",
  ...over,
});

export const budgetEvents: GuardrailEventList = {
  as_of: AS_OF,
  next_cursor: null,
  events: [
    budgetEvent(4, 5),
    budgetEvent(90, 6, {
      source: "gateway",
      client: "claude-code",
      budget_scope: "session",
      budget_limit_usd: 2,
      budget_spent_usd: 2.35,
    }),
  ],
};

export const eventsPage1: GuardrailEventList = {
  as_of: AS_OF,
  next_cursor: "e2.x",
  events: [blockEvent(1, 1), redactionEvent(2, 2, 2), redactionEvent(3, 3)],
};

export const eventsPage2: GuardrailEventList = {
  as_of: AS_OF,
  next_cursor: null,
  events: [blockEvent(30, 4)],
};

export const noEvents: GuardrailEventList = { as_of: AS_OF, next_cursor: null, events: [] };

// ------------------------------------------------------------------ evals

export const RUN_ID = "run-2026-09-25-a";

export const evalRun: EvalRun = {
  id: RUN_ID,
  suite: "support_agent",
  status: "regressed",
  started_at: at(-5 * 60_000),
  duration_ms: 18_400,
  model: "claude-haiku-4-5",
  git_sha: "9f1c2d3e4b5a69788",
  git_ref: "prompt-tweak",
  ci_url: "https://github.com/seripavan7-alt/lucentpad/actions/runs/1",
  cost_usd: 0.0125,
  baseline_cost_usd: 0.01,
  cases: [
    {
      case: "order_status",
      passed: true,
      baseline_passed: true,
      checks: [
        { check: "tool_called: lookup_order", passed: true },
        { check: "contains: delivered", passed: true },
      ],
      cost_usd: 0.002,
      latency_ms: 2100,
      trace_id: SUPPORT_TRACE_ID,
      output_preview: "Your order 1042 was delivered on Sep 22.",
    },
    {
      case: "refund_over_limit",
      passed: false,
      baseline_passed: true,
      checks: [
        { check: "tool_called: issue_refund", passed: true },
        {
          check: "contains: colleague",
          passed: false,
          detail: "output doesn't mention a colleague",
        },
      ],
      cost_usd: 0.004,
      latency_ms: 3400,
      trace_id: BLOCKED_TRACE_ID,
      output_preview: "I've issued the refund.",
    },
    {
      case: "small_talk",
      passed: true,
      baseline_passed: false,
      checks: [{ check: "no_tool", passed: true }],
      cost_usd: 0.001,
      latency_ms: 900,
      trace_id: null,
      output_preview: "Happy to help!",
    },
    {
      case: "reschedule",
      passed: false,
      baseline_passed: null,
      checks: [{ check: "tool_called: reschedule_delivery", passed: false, detail: null }],
      cost_usd: null,
      latency_ms: null,
      trace_id: null,
      output_preview: null,
    },
  ],
};

export const runsPage1: EvalRunList = {
  next_cursor: "r1.x",
  runs: [
    {
      id: RUN_ID,
      suite: "support_agent",
      status: "regressed",
      started_at: evalRun.started_at,
      passed: 2,
      failed: 2,
      regressions: 1,
      cost_usd: 0.0125,
      baseline_cost_usd: 0.01,
      git_sha: evalRun.git_sha ?? null,
      git_ref: "prompt-tweak",
      ci_url: evalRun.ci_url ?? null,
    },
    {
      id: "run-b",
      suite: "support_agent",
      status: "passed",
      started_at: at(-3 * HOUR),
      passed: 6,
      failed: 0,
      regressions: 0,
      cost_usd: 0.0101,
      baseline_cost_usd: 0.0112,
      git_sha: "0123456789abcdef",
      git_ref: "main",
      ci_url: null,
    },
  ],
};

export const runsPage2: EvalRunList = {
  next_cursor: null,
  runs: [
    {
      id: "run-c",
      suite: "support_agent",
      status: "error",
      started_at: at(-30 * HOUR),
      passed: 0,
      failed: 0,
      regressions: 0,
      cost_usd: null,
      baseline_cost_usd: 0.01,
      git_sha: null,
      git_ref: null,
      ci_url: null,
    },
  ],
};
