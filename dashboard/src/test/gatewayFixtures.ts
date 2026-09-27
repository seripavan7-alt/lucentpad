/**
 * Gateway page fixtures: two interleaved sessions (Claude Code with a failover turn, Copilot
 * Chat) and a Copilot CLI session on page 2.
 */
import type { GatewayTurn, GatewayTurnList, GatewaySummary } from "../api/types";
import { at } from "./fixtures";

export const CC_SESSION = "cc000000000000000000000000000001";
export const CHAT_SESSION = "c4a70000000000000000000000000002";
export const CLI_SESSION = "c1100000000000000000000000000003";

export function turn(
  spanId: string,
  traceId: string,
  offsetMs: number,
  extra: Partial<GatewayTurn> = {},
): GatewayTurn {
  return {
    trace_id: traceId,
    span_id: spanId,
    client: "claude-code",
    provider: "anthropic",
    model: "claude-sonnet-5",
    start_time: at(offsetMs),
    duration_ms: 4200,
    ttfb_ms: 380,
    status: "ok",
    streaming: true,
    input_tokens: 12_000,
    output_tokens: 400,
    cost_usd: 0.028,
    failover: false,
    input_preview: "Add type hints to store.py.",
    output_preview: "Done: store.py now passes mypy --strict.",
    ...extra,
  };
}

// Newest first, the Claude Code and Copilot Chat sessions interleaved.
export const turnsPage1: GatewayTurnList = {
  turns: [
    turn("aa00000000000003", CC_SESSION, -60_000, {
      model: "claude-haiku-4-5",
      failover: true,
      cost_usd: 0.0121,
      input_preview: "Run the tests again.",
      output_preview: "All 42 tests pass.",
    }),
    turn("bb00000000000002", CHAT_SESSION, -120_000, {
      client: "copilot-chat",
      provider: "openai",
      model: "gpt-5",
      ttfb_ms: null,
      streaming: false,
      cost_usd: 0.0105,
      input_tokens: 8000,
      output_tokens: 250,
      input_preview: "Explain this regex",
      output_preview: "It matches a W3C trace id.",
    }),
    turn("aa00000000000002", CC_SESSION, -180_000, { status: "error", cost_usd: null }),
    turn("aa00000000000001", CC_SESSION, -240_000),
  ],
  next_cursor: "cursor-2",
  as_of: "2026-09-25T10:10:00Z",
};

export const turnsPage2: GatewayTurnList = {
  turns: [
    turn("cc00000000000001", CLI_SESSION, -3_600_000, {
      client: "copilot-cli",
      input_preview: "Fix the flaky test",
      output_preview: "Added a retry.",
    }),
  ],
  next_cursor: null,
  as_of: "2026-09-25T10:10:00Z",
};

export const gatewaySummary: GatewaySummary = {
  // The API's order (most expensive first); the page shows a fixed client order.
  clients: [
    {
      client: "claude-code",
      sessions: 3,
      turns: 41,
      input_tokens: 1_250_000,
      output_tokens: 48_200,
      cost_usd: 3.2156,
    },
    {
      client: "copilot-cli",
      sessions: 1,
      turns: 6,
      input_tokens: 64_000,
      output_tokens: 2100,
      cost_usd: 0.1504,
    },
    {
      client: "copilot-chat",
      sessions: 2,
      turns: 9,
      input_tokens: 72_000,
      output_tokens: 2250,
      cost_usd: 0.0945,
    },
  ],
  as_of: "2026-09-25T10:10:00Z",
};

export const emptyTurns: GatewayTurnList = {
  turns: [],
  next_cursor: null,
  as_of: "2026-09-25T10:10:00Z",
};
export const emptySummary: GatewaySummary = { clients: [], as_of: "2026-09-25T10:10:00Z" };
