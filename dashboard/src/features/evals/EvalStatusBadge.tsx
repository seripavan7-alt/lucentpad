import type { EvalStatus } from "../../api/types";
import styles from "./Evals.module.css";
import { EVAL_STATUS_LABELS } from "./evals";

export function EvalStatusBadge({ status }: { status: EvalStatus }) {
  return (
    <span className={styles.badge} data-status={status}>
      <span className={styles.dot} aria-hidden="true" />
      {EVAL_STATUS_LABELS[status]}
    </span>
  );
}

/** Pass / Fail for one case or check. */
export function PassBadge({ passed }: { passed: boolean }) {
  return (
    <span className={styles.badge} data-status={passed ? "passed" : "failed"}>
      <span className={styles.dot} aria-hidden="true" />
      {passed ? "Pass" : "Fail"}
    </span>
  );
}
