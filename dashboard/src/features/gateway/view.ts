/*
 * The Gateway page's view state, kept in the URL like the Traces page's:
 *   ?range=1h              time-range preset (shared presets; default 24h, omitted)
 *   ?client=claude-code    one client (claude-code, copilot-chat, copilot-cli, other); omitted = all
 * Paging is not in the URL: any change here starts again from the newest turns.
 */
import { useSearchParams } from "react-router";
import { GATEWAY_CLIENTS, type GatewayClient } from "../../api/types";
import { DEFAULT_RANGE, RANGES, type RangeId } from "../traces/view";

export interface GatewayView {
  range: RangeId;
  /** null = every client. */
  client: GatewayClient | null;
}

export function parseGatewayView(params: URLSearchParams): GatewayView {
  const rawRange = params.get("range");
  const range = RANGES.find((r) => r.id === rawRange)?.id ?? DEFAULT_RANGE;
  const rawClient = params.get("client");
  const client = GATEWAY_CLIENTS.find((c) => c === rawClient) ?? null;
  return { range, client };
}

export function applyGatewayPatch(
  prev: URLSearchParams,
  patch: Partial<GatewayView>,
): URLSearchParams {
  const next = new URLSearchParams(prev);
  if (patch.range !== undefined) {
    if (patch.range === DEFAULT_RANGE) next.delete("range");
    else next.set("range", patch.range);
  }
  if (patch.client !== undefined) {
    if (patch.client === null) next.delete("client");
    else next.set("client", patch.client);
  }
  return next;
}

export function useGatewayView(): [GatewayView, (patch: Partial<GatewayView>) => void] {
  const [params, setParams] = useSearchParams();
  const view = parseGatewayView(params);
  const update = (patch: Partial<GatewayView>) => {
    setParams((prev) => applyGatewayPatch(prev, patch));
  };
  return [view, update];
}

/** The client filter as request params (repeated `client`, here at most one). */
export function clientParams(view: GatewayView): { client?: string[] } {
  return view.client === null ? {} : { client: [view.client] };
}
