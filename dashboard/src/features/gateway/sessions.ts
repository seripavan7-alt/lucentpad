import {
  GATEWAY_CLIENTS,
  type GatewayClientTotals,
  type GatewaySummary,
  type GatewayTurn,
} from "../../api/types";
import { isoMicros } from "../../lib/time";

/** The loaded turns of one session trace, newest first. */
export interface SessionGroup {
  traceId: string;
  client: string | null;
  turns: GatewayTurn[];
  /** Start of the session's oldest loaded turn. */
  started: string;
  /** Start of its newest loaded turn. */
  latest: string;
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
}

/**
 * Group a newest-first turn list by session (trace), sessions in order of first appearance
 * (so by their newest turn).
 * Interleaved sessions (two assistants at once) stay one group each. The totals cover only the
 * loaded turns: the API has no per-session aggregate, so a session that began before the
 * window (or on a page not loaded yet) grows as more turns load.
 */
export function groupSessions(turns: readonly GatewayTurn[]): SessionGroup[] {
  const groups = new Map<string, SessionGroup>();
  for (const turn of turns) {
    let group = groups.get(turn.trace_id);
    if (!group) {
      group = {
        traceId: turn.trace_id,
        client: turn.client,
        turns: [],
        started: turn.start_time,
        latest: turn.start_time,
        costUsd: 0,
        inputTokens: 0,
        outputTokens: 0,
      };
      groups.set(turn.trace_id, group);
    }
    group.turns.push(turn);
    if (isoMicros(turn.start_time) < isoMicros(group.started)) group.started = turn.start_time;
    if (isoMicros(turn.start_time) > isoMicros(group.latest)) group.latest = turn.start_time;
    group.client ??= turn.client;
    group.costUsd += turn.cost_usd ?? 0;
    group.inputTokens += turn.input_tokens ?? 0;
    group.outputTokens += turn.output_tokens ?? 0;
  }
  return [...groups.values()];
}

function empty(client: string): GatewayClientTotals {
  return { client, sessions: 0, turns: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 };
}

/** One tile per known client in a fixed order (zeros included), whatever the API's order. */
export function totalsByClient(summary: GatewaySummary | undefined): GatewayClientTotals[] {
  const byClient = new Map(summary?.clients.map((c) => [c.client, c]));
  return GATEWAY_CLIENTS.map((client) => byClient.get(client) ?? empty(client));
}

/** A turn opens its session trace with its span selected; a session header opens the trace. */
export const turnHref = (turn: Pick<GatewayTurn, "trace_id" | "span_id">) =>
  `/traces/${turn.trace_id}?span=${turn.span_id}`;
export const sessionHref = (traceId: string) => `/traces/${traceId}`;
