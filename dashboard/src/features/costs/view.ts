/*
 * The Costs page's view state, kept in the URL like the Traces page's:
 *   ?range=7d        time-range preset (shared presets; default 24h, omitted)
 *   ?group=client    what the spend is stacked by: model (default, omitted), client, service
 */
import { useSearchParams } from "react-router";
import { COST_GROUPS, type CostGroup } from "../../api/types";
import { DEFAULT_RANGE, RANGES, type RangeId } from "../traces/view";

export const DEFAULT_GROUP: CostGroup = "model";

export const GROUP_LABELS: Record<CostGroup, string> = {
  model: "Model",
  client: "Client",
  service: "Service",
};

export interface CostsView {
  range: RangeId;
  group: CostGroup;
}

export function parseCostsView(params: URLSearchParams): CostsView {
  const rawRange = params.get("range");
  const range = RANGES.find((r) => r.id === rawRange)?.id ?? DEFAULT_RANGE;
  const rawGroup = params.get("group");
  const group = COST_GROUPS.find((g) => g === rawGroup) ?? DEFAULT_GROUP;
  return { range, group };
}

export function applyCostsPatch(prev: URLSearchParams, patch: Partial<CostsView>): URLSearchParams {
  const next = new URLSearchParams(prev);
  if (patch.range !== undefined) {
    if (patch.range === DEFAULT_RANGE) next.delete("range");
    else next.set("range", patch.range);
  }
  if (patch.group !== undefined) {
    if (patch.group === DEFAULT_GROUP) next.delete("group");
    else next.set("group", patch.group);
  }
  return next;
}

export function useCostsView(): [CostsView, (patch: Partial<CostsView>) => void] {
  const [params, setParams] = useSearchParams();
  const view = parseCostsView(params);
  const update = (patch: Partial<CostsView>) => {
    setParams((prev) => applyCostsPatch(prev, patch));
  };
  return [view, update];
}
