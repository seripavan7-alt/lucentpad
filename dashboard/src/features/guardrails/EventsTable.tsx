import type { CSSProperties, MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { GuardrailEvent } from "../../api/types";
import table from "../../components/DataTable.module.css";
import {
  clientLabel,
  formatDateTime,
  formatIsoUtc,
  formatRelative,
  shortId,
} from "../../lib/format";
import { useNow } from "../../lib/useNow";
import styles from "./Guardrails.module.css";
import { budgetScope, budgetSpend, eventHref, eventKey, redactionLabel } from "./view";

const KIND_LABELS: Record<GuardrailEvent["kind"], string> = {
  block: "Blocked",
  redaction: "Redacted",
  budget: "Budget",
};

/** The "What" cell: the rule and reason, the redacted value kind, or spend against the limit. */
function EventWhat({ e }: { e: GuardrailEvent }) {
  if (e.kind === "block") {
    return (
      <>
        <span className="mono">{e.rule ?? "unknown rule"}</span>
        {e.reason && <span className={table.secondary}> · {e.reason}</span>}
      </>
    );
  }
  if (e.kind === "budget") {
    return (
      <>
        Budget alert
        <span className={`${table.secondary} num`}>
          {" "}
          · {budgetSpend(e)} ({budgetScope(e)})
        </span>
      </>
    );
  }
  return (
    <>
      {redactionLabel(e.redaction_kind)}
      {e.count > 1 && <span className={`${table.secondary} num`}> × {e.count}</span>}
    </>
  );
}

interface Props {
  events: GuardrailEvent[];
  /** Event keys that just arrived by live polling (briefly highlighted). */
  fresh?: ReadonlySet<string>;
  stale?: boolean;
}

const sourceLabel = (e: GuardrailEvent) => {
  const via = e.source === "gateway" ? "Gateway" : "SDK";
  const client = e.client && e.client !== "sdk" ? clientLabel(e.client) : null;
  return client ? `${via} · ${client}` : via;
};

/** Blocks, redactions and budget alerts, newest first; a row opens the trace with the span selected. */
export function EventsTable({ events, fresh, stale }: Props) {
  const navigate = useNavigate();
  const now = useNow();
  const go = (href: string) => (e: MouseEvent) => {
    if (e.target instanceof Element && e.target.closest("a")) return;
    void navigate(href);
  };

  return (
    <div className={table.wrap} data-stale={stale ? "true" : undefined}>
      <table className={table.table} style={{ "--table-min": "760px" } as CSSProperties}>
        <thead>
          <tr>
            <th scope="col" style={{ width: 148 }}>
              Time
            </th>
            <th scope="col" style={{ width: 112 }}>
              Kind
            </th>
            <th scope="col">What</th>
            <th scope="col" style={{ width: 176 }}>
              Source
            </th>
            <th scope="col" style={{ width: 104 }}>
              Trace
            </th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const key = eventKey(e);
            const href = eventHref(e);
            return (
              <tr
                key={key}
                className={table.row}
                data-event-key={key}
                data-kind={e.kind}
                data-fresh={fresh?.has(key) ? "true" : undefined}
                onClick={go(href)}
              >
                <td
                  className={`${table.secondary} num`}
                  title={`${formatRelative(e.time, now)} · ${formatIsoUtc(e.time)}`}
                >
                  <time dateTime={e.time}>{formatDateTime(e.time)}</time>
                </td>
                <td>
                  <span className={styles.kind} data-kind={e.kind}>
                    {KIND_LABELS[e.kind]}
                  </span>
                </td>
                <td className={table.clip} title={e.reason ?? undefined}>
                  <EventWhat e={e} />
                </td>
                <td className={`${table.clip} ${table.secondary}`}>{sourceLabel(e)}</td>
                <td>
                  <Link to={href} className={`mono ${table.secondary} ${table.link}`}>
                    {shortId(e.trace_id)}
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
