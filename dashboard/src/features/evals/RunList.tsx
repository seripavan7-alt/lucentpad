import { useEffect, useRef } from "react";
import { Link } from "react-router";
import type { EvalRunSummary } from "../../api/types";
import { formatCost, formatDateTime, formatIsoUtc, formatRelative } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { EvalStatusBadge } from "./EvalStatusBadge";
import styles from "./Evals.module.css";
import { costDelta, runHref, shortSha } from "./evals";

/** One small square per case: regressions, then other failures, then passes. */
export function CaseDots({
  passed,
  failed,
  regressions,
}: {
  passed: number;
  failed: number;
  regressions: number;
}) {
  const otherFailed = Math.max(0, failed - regressions);
  const dots = [
    ...Array<string>(regressions).fill("regressed"),
    ...Array<string>(otherFailed).fill("failed"),
    ...Array<string>(passed).fill("passed"),
  ];
  return (
    <span className={styles.dots} aria-hidden="true">
      {dots.slice(0, 40).map((kind, i) => (
        <i key={i} data-kind={kind} />
      ))}
    </span>
  );
}

/** The runs, newest first. The selected run is highlighted and points at the run panel. */
export function RunList({
  runs,
  selectedId,
}: {
  runs: EvalRunSummary[];
  selectedId: string | null;
}) {
  const now = useNow();
  const selectedRef = useRef<HTMLAnchorElement | null>(null);

  useEffect(() => {
    const el = selectedRef.current;
    // jsdom has no scrollIntoView.
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  return (
    <ol className={styles.runList} aria-label="Eval runs">
      {runs.map((run) => {
        const selected = run.id === selectedId;
        const total = run.passed + run.failed;
        const sha = shortSha(run.git_sha);
        const delta = costDelta(run.cost_usd, run.baseline_cost_usd ?? null);
        return (
          <li key={run.id}>
            <Link
              ref={selected ? selectedRef : undefined}
              to={runHref(run.id)}
              className={styles.runItem}
              data-run-id={run.id}
              data-status={run.status}
              aria-current={selected ? "true" : undefined}
            >
              <span className={styles.runTop}>
                <time
                  dateTime={run.started_at}
                  className={`num ${styles.runTime}`}
                  title={`${formatRelative(run.started_at, now)} · ${formatIsoUtc(run.started_at)}`}
                >
                  {formatDateTime(run.started_at)}
                </time>
                <EvalStatusBadge status={run.status} />
              </span>
              <span className={styles.runSuite}>
                {run.suite}
                {(run.git_ref ?? sha) && (
                  <span className={styles.runCommit}>
                    {run.git_ref}
                    {run.git_ref && sha && " · "}
                    {sha && <span className="mono">{sha}</span>}
                  </span>
                )}
              </span>
              <span className={styles.runStats}>
                <span className="num">
                  <strong>{run.passed}</strong>/{total} passed
                </span>
                {run.regressions > 0 && (
                  <span className={styles.runRegressions}>
                    {run.regressions} regression{run.regressions === 1 ? "" : "s"}
                  </span>
                )}
                <span className={`num ${styles.runCost}`}>
                  {run.cost_usd === null ? "–" : formatCost(run.cost_usd)}
                  {delta && <span className={styles.runDelta}> {delta}</span>}
                </span>
              </span>
              <CaseDots passed={run.passed} failed={run.failed} regressions={run.regressions} />
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
