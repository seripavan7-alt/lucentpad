import type { CSSProperties, MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { EvalRunSummary } from "../../api/types";
import table from "../../components/DataTable.module.css";
import { formatCost, formatDateTime, formatIsoUtc, formatRelative } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { EvalStatusBadge } from "./EvalStatusBadge";
import styles from "./Evals.module.css";
import { costDelta, runHref, shortSha } from "./evals";

const costDirection = (delta: string) =>
  delta.startsWith("+") ? "up" : delta.startsWith("−") ? "down" : "same";

/** Eval runs, newest first; a row opens the run. */
export function RunsTable({ runs }: { runs: EvalRunSummary[] }) {
  const navigate = useNavigate();
  const now = useNow();
  const go = (href: string) => (e: MouseEvent) => {
    if (e.target instanceof Element && e.target.closest("a")) return;
    void navigate(href);
  };
  return (
    <div className={table.wrap}>
      <table className={table.table} style={{ "--table-min": "1060px" } as CSSProperties}>
        <thead>
          <tr>
            <th scope="col" style={{ width: 120 }}>
              Status
            </th>
            <th scope="col">Suite</th>
            <th scope="col" style={{ width: 148 }}>
              Started
            </th>
            <th scope="col" className={table.right} style={{ width: 104 }}>
              Passed
            </th>
            <th scope="col" className={table.right} style={{ width: 104 }}>
              Regressions
            </th>
            <th scope="col" className={table.right} style={{ width: 152 }}>
              Cost vs baseline
            </th>
            <th scope="col" style={{ width: 200 }}>
              Commit
            </th>
            <th scope="col" style={{ width: 64 }}>
              CI
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const href = runHref(run.id);
            const total = run.passed + run.failed;
            const sha = shortSha(run.git_sha);
            const baseline = run.baseline_cost_usd ?? null;
            const delta = costDelta(run.cost_usd, baseline);
            return (
              <tr key={run.id} className={table.row} data-run-id={run.id} onClick={go(href)}>
                <td>
                  <EvalStatusBadge status={run.status} />
                </td>
                <td className={table.clip}>
                  <Link to={href} className={table.link}>
                    {run.suite}
                  </Link>
                </td>
                <td
                  className={`${table.secondary} num`}
                  title={`${formatRelative(run.started_at, now)} · ${formatIsoUtc(run.started_at)}`}
                >
                  <time dateTime={run.started_at}>{formatDateTime(run.started_at)}</time>
                </td>
                <td className={`${table.right} num`}>
                  {run.passed}
                  <span className={table.tertiary}> / {total}</span>
                </td>
                <td className={`${table.right} num`}>
                  {run.regressions > 0 ? (
                    <span className={styles.regressions}>{run.regressions}</span>
                  ) : (
                    <span className={table.tertiary}>0</span>
                  )}
                </td>
                <td
                  className={`${table.right} num`}
                  title={
                    baseline === null ? "no baseline cost" : `baseline ${formatCost(baseline)}`
                  }
                >
                  {run.cost_usd === null ? (
                    <span className={table.tertiary}>–</span>
                  ) : (
                    formatCost(run.cost_usd)
                  )}
                  {delta && (
                    <span className={styles.costDelta} data-direction={costDirection(delta)}>
                      {delta}
                    </span>
                  )}
                </td>
                <td className={`${table.clip} ${table.secondary}`}>
                  {run.git_ref ?? (sha ? null : <span className={table.tertiary}>–</span>)}
                  {run.git_ref && sha && <span className={table.tertiary}> · </span>}
                  {sha && <span className="mono">{sha}</span>}
                </td>
                <td>
                  {run.ci_url ? (
                    <a
                      href={run.ci_url}
                      target="_blank"
                      rel="noreferrer"
                      className={`${table.secondary} ${table.link}`}
                    >
                      Open
                    </a>
                  ) : (
                    <span className={table.tertiary}>–</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
