import type { CSSProperties } from "react";
import { Link } from "react-router";
import type { EvalCaseResult } from "../../api/types";
import table from "../../components/DataTable.module.css";
import { formatCost, formatDuration } from "../../lib/format";
import { PassBadge } from "./EvalStatusBadge";
import styles from "./Evals.module.css";
import { caseChange, type CaseChange } from "./evals";

function Baseline({ c, change }: { c: EvalCaseResult; change: CaseChange }) {
  if (change === "new") return <span className={table.tertiary}>New case</span>;
  return (
    <span className={styles.baseline}>
      <span className={table.secondary}>{c.baseline_passed ? "Passed" : "Failed"}</span>
      {change === "regressed" && <span className={styles.regressedTag}>Regression</span>}
      {change === "fixed" && <span className={styles.fixedTag}>Fixed</span>}
    </span>
  );
}

/**
 * One row per case (regressions first, then failures, then the rest, each in suite order),
 * with the failed checks spelled out under a failing case.
 */
export function CaseTable({ cases }: { cases: EvalCaseResult[] }) {
  const rank = (c: EvalCaseResult) => {
    const change = caseChange(c);
    return change === "regressed" ? 0 : !c.passed ? 1 : 2;
  };
  const ordered = cases
    .map((c, i) => ({ c, i }))
    .sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i);

  return (
    <div className={table.wrap}>
      <table className={table.table} style={{ "--table-min": "980px" } as CSSProperties}>
        <thead>
          <tr>
            <th scope="col" style={{ width: 88 }}>
              Result
            </th>
            <th scope="col" style={{ width: "22%" }}>
              Case
            </th>
            <th scope="col" style={{ width: 176 }}>
              Baseline
            </th>
            <th scope="col" className={table.right} style={{ width: 72 }}>
              Checks
            </th>
            <th scope="col">Output</th>
            <th scope="col" className={table.right} style={{ width: 88 }}>
              Latency
            </th>
            <th scope="col" className={table.right} style={{ width: 88 }}>
              Cost
            </th>
            <th scope="col" style={{ width: 64 }}>
              Trace
            </th>
          </tr>
        </thead>
        {ordered.map(({ c }) => {
          const change = caseChange(c);
          const failed = c.checks.filter((k) => !k.passed);
          const passedChecks = c.checks.length - failed.length;
          return (
            <tbody
              key={c.case}
              className={styles.caseGroup}
              data-case={c.case}
              data-change={change}
              data-passed={c.passed ? "true" : "false"}
            >
              <tr>
                <td>
                  <PassBadge passed={c.passed} />
                </td>
                <th scope="row" className={table.clip} title={c.case}>
                  {c.case}
                </th>
                <td>
                  <Baseline c={c} change={change} />
                </td>
                <td className={`${table.right} num`}>
                  {passedChecks}
                  <span className={table.tertiary}> / {c.checks.length}</span>
                </td>
                <td
                  className={`${table.clip} ${table.secondary}`}
                  title={c.output_preview ?? undefined}
                >
                  {c.output_preview?.replace(/\s+/g, " ") ?? (
                    <span className={table.tertiary}>–</span>
                  )}
                </td>
                <td className={`${table.right} num`}>
                  {c.latency_ms === null ? (
                    <span className={table.tertiary}>–</span>
                  ) : (
                    formatDuration(c.latency_ms)
                  )}
                </td>
                <td className={`${table.right} num`}>
                  {c.cost_usd === null ? (
                    <span className={table.tertiary}>–</span>
                  ) : (
                    formatCost(c.cost_usd)
                  )}
                </td>
                <td>
                  {c.trace_id ? (
                    <Link
                      to={`/traces/${c.trace_id}`}
                      className={`${table.secondary} ${table.link}`}
                    >
                      Open
                    </Link>
                  ) : (
                    <span className={table.tertiary}>–</span>
                  )}
                </td>
              </tr>
              {failed.length > 0 && (
                <tr className={styles.checksRow}>
                  <td />
                  <td colSpan={7}>
                    <ul className={styles.checks} aria-label={`Failed checks for ${c.case}`}>
                      {failed.map((k, i) => (
                        <li key={`${k.check}.${i}`} className={styles.check}>
                          <span className={`mono ${styles.checkName}`}>{k.check}</span>
                          {k.detail && <span className={styles.checkDetail}>{k.detail}</span>}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              )}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
