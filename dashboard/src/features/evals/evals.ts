import type { EvalCaseResult, EvalStatus } from "../../api/types";

/** What the Evals empty state tells people to run. */
export const EVAL_COMMAND = "lucentpad eval evals/support_agent.yaml";

export const EVAL_STATUS_LABELS: Record<EvalStatus, string> = {
  passed: "Passed",
  failed: "Failed",
  regressed: "Regressed",
  error: "Error",
};

/** How a case compares with the baseline. */
export type CaseChange = "regressed" | "fixed" | "new" | "same";

export function caseChange(c: Pick<EvalCaseResult, "passed" | "baseline_passed">): CaseChange {
  if (c.baseline_passed === null) return "new";
  if (c.baseline_passed && !c.passed) return "regressed";
  if (!c.baseline_passed && c.passed) return "fixed";
  return "same";
}

/** Signed change against the baseline, e.g. "+12%" / "−8%"; null when either side is missing. */
export function costDelta(cost: number | null, baseline: number | null): string | null {
  if (cost === null || baseline === null || baseline <= 0) return null;
  const pct = Math.round(((cost - baseline) / baseline) * 100);
  if (pct === 0) return "±0%";
  return pct > 0 ? `+${pct}%` : `−${Math.abs(pct)}%`;
}

export const shortSha = (sha: string | null | undefined) => (sha ? sha.slice(0, 7) : null);

export const runHref = (id: string) => `/evals/${encodeURIComponent(id)}`;
