import type { GatewayClient, GatewaySummary } from "../../api/types";
import { clientLabel, formatCost, formatInteger, formatTokens } from "../../lib/format";
import styles from "./ClientTotals.module.css";
import { totalsByClient } from "./sessions";

const plural = (n: number, one: string, many: string) =>
  `${formatInteger(n)} ${n === 1 ? one : many}`;

interface Props {
  summary: GatewaySummary | undefined;
  /** The client filter: its tile is marked. */
  selected: GatewayClient | null;
  loading: boolean;
}

export function ClientTotals({ summary, selected, loading }: Props) {
  const totals = totalsByClient(summary);
  return (
    <section className={styles.totals} aria-label="Totals by client" aria-busy={loading}>
      {totals.map((t) => (
        <div
          key={t.client}
          className={styles.tile}
          role="group"
          aria-label={clientLabel(t.client) ?? t.client}
          data-selected={selected === t.client ? "true" : undefined}
          data-empty={t.turns === 0 ? "true" : undefined}
        >
          <span className={styles.label}>{clientLabel(t.client)}</span>
          <span className={`${styles.cost} num`}>{loading ? "–" : formatCost(t.cost_usd)}</span>
          <span className={`${styles.meta} num`}>
            {plural(t.sessions, "session", "sessions")} · {plural(t.turns, "turn", "turns")}
          </span>
          <span className={`${styles.meta} ${styles.tokens} num`}>
            {formatTokens(t.input_tokens)} in · {formatTokens(t.output_tokens)} out
          </span>
        </div>
      ))}
    </section>
  );
}
