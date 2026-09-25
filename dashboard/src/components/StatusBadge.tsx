import type { SpanStatus } from "../api/types";
import { statusLabel } from "../lib/labels";
import styles from "./StatusBadge.module.css";

export function StatusBadge({ status }: { status: SpanStatus }) {
  return (
    <span className={styles.badge} data-status={status}>
      <span className={styles.dot} aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}
