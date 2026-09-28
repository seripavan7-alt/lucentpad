import type { CSSProperties, MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { GuardrailEvent } from "../../api/types";
import table from "../../components/DataTable.module.css";
import {
  formatCost,
  formatDateTime,
  formatIsoUtc,
  formatRelative,
  shortId,
} from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { budgetScope, eventHref, eventKey } from "../guardrails/view";

const SCOPE_LABELS: Record<string, string> = { run: "Run", session: "Session" };

const cost = (usd: number | null | undefined) =>
  usd == null ? <span className={table.tertiary}>–</span> : formatCost(usd);

/** Budget alerts for the range, newest first; a row opens the trace at the alerting span. */
export function BudgetAlerts({ alerts, stale }: { alerts: GuardrailEvent[]; stale?: boolean }) {
  const navigate = useNavigate();
  const now = useNow();
  const go = (href: string) => (e: MouseEvent) => {
    if (e.target instanceof Element && e.target.closest("a")) return;
    void navigate(href);
  };
  return (
    <div className={table.wrap} data-stale={stale ? "true" : undefined}>
      <table className={table.table} style={{ "--table-min": "640px" } as CSSProperties}>
        <thead>
          <tr>
            <th scope="col" style={{ width: 148 }}>
              When
            </th>
            <th scope="col">Trace</th>
            <th scope="col" style={{ width: 104 }}>
              Scope
            </th>
            <th scope="col" className={table.right} style={{ width: 200 }}>
              Spent / limit
            </th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => {
            const key = eventKey(a);
            const href = eventHref(a);
            const scope = budgetScope(a);
            return (
              <tr key={key} className={table.row} data-event-key={key} onClick={go(href)}>
                <td
                  className={`${table.secondary} num`}
                  title={`${formatRelative(a.time, now)} · ${formatIsoUtc(a.time)}`}
                >
                  <time dateTime={a.time}>{formatDateTime(a.time)}</time>
                </td>
                <td className={table.clip}>
                  <Link to={href} className={`mono ${table.link}`}>
                    {shortId(a.trace_id)}
                  </Link>
                </td>
                <td className={table.secondary}>{SCOPE_LABELS[scope] ?? scope}</td>
                <td className={`${table.right} num`}>
                  {cost(a.budget_spent_usd)}
                  <span className={table.tertiary}> / </span>
                  <span className={table.secondary}>{cost(a.budget_limit_usd)}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
