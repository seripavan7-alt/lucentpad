import { useId, useState } from "react";
import { Link } from "react-router";
import type { EvalCaseResult } from "../../api/types";
import { formatCost, formatDuration } from "../../lib/format";
import styles from "./Evals.module.css";
import { caseChange, orderCases, type CaseChange } from "./evals";

const CHANGE_TAG: Partial<Record<CaseChange, string>> = {
  regressed: "Regression",
  fixed: "Fixed",
  new: "New case",
};

function CaseCard({ c, index }: { c: EvalCaseResult; index: number }) {
  const change = caseChange(c);
  const [open, setOpen] = useState(!c.passed);
  const bodyId = useId();
  const passedChecks = c.checks.filter((k) => k.passed).length;
  const tag = CHANGE_TAG[change];

  return (
    <li
      className={styles.caseCard}
      data-case={c.case}
      data-change={change}
      data-passed={c.passed ? "true" : "false"}
      data-open={open ? "true" : "false"}
    >
      <button
        type="button"
        className={styles.caseHead}
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        <span className={styles.caseIcon} aria-hidden="true">
          {c.passed ? "✓" : "✕"}
        </span>
        <span className={styles.caseIndex} aria-hidden="true">
          {String(index + 1).padStart(2, "0")}
        </span>
        <span className={styles.caseName}>{c.case}</span>
        <span className="visually-hidden">{c.passed ? "passed" : "failed"}</span>
        {tag && (
          <span className={styles.changeTag} data-change={change}>
            {tag}
          </span>
        )}
        <span className={styles.caseMeta}>
          <span className="num">
            {passedChecks}/{c.checks.length} checks
          </span>
          <span className="num">{c.latency_ms === null ? "–" : formatDuration(c.latency_ms)}</span>
          <span className="num">{c.cost_usd === null ? "–" : formatCost(c.cost_usd)}</span>
        </span>
        <span className={styles.chevron} aria-hidden="true" />
      </button>
      <div id={bodyId} className={styles.caseBody} hidden={!open}>
        <div className={styles.caseColumns}>
          <section aria-label={`Output of ${c.case}`}>
            <h4 className={styles.blockLabel}>Agent output</h4>
            {c.output_preview ? (
              <p className={styles.output}>{c.output_preview}</p>
            ) : (
              <p className={styles.muted}>No output recorded.</p>
            )}
          </section>
          <section aria-label={`Checks for ${c.case}`}>
            <h4 className={styles.blockLabel}>Checks</h4>
            <ul className={styles.checkList}>
              {c.checks.map((k, i) => (
                <li key={`${k.check}.${i}`} data-passed={k.passed ? "true" : "false"}>
                  <span className={styles.checkIcon} aria-hidden="true">
                    {k.passed ? "✓" : "✕"}
                  </span>
                  <span>
                    <span className={`mono ${styles.checkName}`}>{k.check}</span>
                    {k.detail && <span className={styles.checkDetail}>{k.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>
        <footer className={styles.caseFoot}>
          <span>
            Baseline{" "}
            <strong>
              {c.baseline_passed === null ? "none" : c.baseline_passed ? "passed" : "failed"}
            </strong>
            <span aria-hidden="true"> → </span>
            now <strong>{c.passed ? "passes" : "fails"}</strong>
          </span>
          {c.trace_id && (
            <Link to={`/traces/${c.trace_id}`} className={styles.traceLink}>
              Open trace →
            </Link>
          )}
        </footer>
      </div>
    </li>
  );
}

export function CaseList({ cases, label }: { cases: EvalCaseResult[]; label: string }) {
  return (
    <ol className={styles.caseList} aria-label={label}>
      {orderCases(cases).map((c, i) => (
        <CaseCard key={c.case} c={c} index={i} />
      ))}
    </ol>
  );
}
