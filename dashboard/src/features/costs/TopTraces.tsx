import type { CSSProperties, MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { TraceSummary } from "../../api/types";
import table from "../../components/DataTable.module.css";
import { StatusBadge } from "../../components/StatusBadge";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatTokens,
  traceOrigin,
} from "../../lib/format";

/** The range's most expensive traces, each opening its trace. */
export function TopTraces({ traces, stale }: { traces: TraceSummary[]; stale?: boolean }) {
  const navigate = useNavigate();
  const go = (href: string) => (e: MouseEvent) => {
    if (e.target instanceof Element && e.target.closest("a")) return;
    void navigate(href);
  };
  return (
    <div className={table.wrap} data-stale={stale ? "true" : undefined}>
      <table className={table.table} style={{ "--table-min": "980px" } as CSSProperties}>
        <thead>
          <tr>
            <th scope="col">Trace</th>
            <th scope="col" style={{ width: 160 }}>
              From
            </th>
            <th scope="col" style={{ width: 148 }}>
              Started
            </th>
            <th scope="col" className={table.right} style={{ width: 72 }}>
              Calls
            </th>
            <th scope="col" className={table.right} style={{ width: 120 }}>
              Tokens in / out
            </th>
            <th scope="col" className={table.right} style={{ width: 92 }}>
              Duration
            </th>
            <th scope="col" className={table.right} style={{ width: 96 }}>
              Cost
            </th>
            <th scope="col" style={{ width: 96 }}>
              Status
            </th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => {
            const href = `/traces/${t.trace_id}`;
            return (
              <tr key={t.trace_id} className={table.row} onClick={go(href)}>
                <td className={table.clip}>
                  <Link to={href} className={table.link}>
                    {t.name}
                  </Link>
                </td>
                <td className={`${table.clip} ${table.secondary}`}>{traceOrigin(t) ?? "–"}</td>
                <td className={`${table.secondary} num`}>
                  <time dateTime={t.start_time}>{formatDateTime(t.start_time)}</time>
                </td>
                <td className={`${table.right} num`}>{formatInteger(t.llm_calls)}</td>
                <td className={`${table.right} num`}>
                  {formatTokens(t.input_tokens)}
                  <span className={table.tertiary}> / </span>
                  {formatTokens(t.output_tokens)}
                </td>
                <td className={`${table.right} num`}>{formatDuration(t.duration_ms)}</td>
                <td className={`${table.right} num`}>{formatCost(t.cost_usd)}</td>
                <td>
                  <StatusBadge status={t.status} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
