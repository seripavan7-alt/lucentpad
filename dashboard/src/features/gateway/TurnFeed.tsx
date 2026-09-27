import { useRef, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { GatewayTurn } from "../../api/types";
import { StatusBadge } from "../../components/StatusBadge";
import {
  clientLabel,
  formatCost,
  formatDateTime,
  formatDuration,
  formatIsoUtc,
  formatRelative,
  formatTokens,
  previewLine,
} from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { useScrollAnchor } from "../../lib/useScrollAnchor";
import { sessionHref, turnHref, type SessionGroup } from "./sessions";
import styles from "./TurnFeed.module.css";

interface Props {
  sessions: SessionGroup[];
  /** Span ids that just arrived by live polling (briefly highlighted). */
  fresh?: ReadonlySet<string>;
  /** The rows are the previous view's, shown while a new range or client loads. */
  stale?: boolean;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const tokens = (n: number | null) => (n === null ? "–" : formatTokens(n));

/**
 * The live turn feed: one row group per session (a header row with the client, when it
 * started, how many turns and what they cost), then its turns, newest first. Rows open the
 * session trace; a turn row selects its span there.
 */
export function TurnFeed({ sessions, fresh, stale }: Props) {
  const navigate = useNavigate();
  const now = useNow();
  const table = useRef<HTMLTableElement>(null);
  useScrollAnchor(table, "data-span-id", sessions[0]?.turns[0]?.span_id ?? "");

  const go = (href: string) => (e: MouseEvent) => {
    if (e.target instanceof Element && e.target.closest("a")) return;
    void navigate(href);
  };
  const timeTitle = (iso: string) => `${formatRelative(iso, now)} · ${formatIsoUtc(iso)}`;

  return (
    <div
      className={styles.wrap}
      aria-busy={stale ? "true" : undefined}
      data-stale={stale ? "true" : undefined}
    >
      <table className={styles.table} ref={table}>
        <thead>
          <tr>
            <th scope="col" className={styles.colTime}>
              Time
            </th>
            <th scope="col" className={styles.colModel}>
              Model
            </th>
            <th scope="col" className={styles.colPreview}>
              Input → Output
            </th>
            <th scope="col" className={`${styles.right} ${styles.colTokens}`}>
              Tokens in / out
            </th>
            <th scope="col" className={`${styles.right} ${styles.colDuration}`}>
              <abbr title="Time to first byte" className={styles.abbr}>
                TTFB
              </abbr>{" "}
              / Duration
            </th>
            <th scope="col" className={`${styles.right} ${styles.colCost}`}>
              Cost
            </th>
            <th scope="col" className={styles.colStatus}>
              Status
            </th>
          </tr>
        </thead>
        {sessions.map((session) => {
          const label = clientLabel(session.client) ?? "Unknown client";
          const href = sessionHref(session.traceId);
          return (
            <tbody key={session.traceId} data-session-id={session.traceId}>
              <tr className={styles.session} onClick={go(href)}>
                <th scope="rowgroup" colSpan={3} className={styles.sessionCell}>
                  <span className={styles.sessionLine}>
                    <Link to={href} className={styles.sessionName}>
                      {label}
                    </Link>
                    <span className={styles.sessionMeta} title={timeTitle(session.started)}>
                      started{" "}
                      <time dateTime={session.started} className="num">
                        {formatDateTime(session.started)}
                      </time>
                    </span>
                    <span className={`${styles.sessionMeta} num`}>
                      {plural(session.turns.length, "turn", "turns")}
                    </span>
                  </span>
                </th>
                <td className={`${styles.right} ${styles.sessionNum} num`}>
                  {formatTokens(session.inputTokens)}
                  <span className={styles.sep}> / </span>
                  {formatTokens(session.outputTokens)}
                </td>
                <td />
                <td className={`${styles.right} ${styles.sessionNum} num`}>
                  {formatCost(session.costUsd)}
                </td>
                <td />
              </tr>
              {session.turns.map((turn) => {
                const turnLink = turnHref(turn);
                const preview = previewLine(turn.input_preview, turn.output_preview);
                return (
                  <tr
                    key={turn.span_id}
                    className={styles.turn}
                    data-span-id={turn.span_id}
                    data-fresh={fresh?.has(turn.span_id) ? "true" : undefined}
                    onClick={go(turnLink)}
                  >
                    <td className={styles.timeCell} title={timeTitle(turn.start_time)}>
                      <Link to={turnLink} className={`${styles.time} num`}>
                        <time dateTime={turn.start_time}>{formatDateTime(turn.start_time)}</time>
                      </Link>
                    </td>
                    <td className={styles.clip}>
                      <span className={styles.model}>
                        <span
                          className={`mono ${styles.modelName}`}
                          title={turn.model ?? undefined}
                        >
                          {turn.model ?? "–"}
                        </span>
                        {turn.failover && (
                          <span
                            className={styles.failover}
                            title="Retried on a fallback model after the first one failed"
                          >
                            failover
                          </span>
                        )}
                      </span>
                    </td>
                    <td
                      className={`${styles.clip} ${styles.preview}`}
                      title={previewTitle(turn) ?? undefined}
                      data-testid="turn-preview"
                    >
                      {preview ?? <span className="muted">–</span>}
                    </td>
                    <td className={`${styles.right} num`}>
                      {tokens(turn.input_tokens)}
                      <span className={styles.sep}> / </span>
                      {tokens(turn.output_tokens)}
                    </td>
                    <td className={`${styles.right} num`}>
                      {turn.ttfb_ms !== null && (
                        <>
                          <span className="muted">{formatDuration(turn.ttfb_ms)}</span>
                          <span className={styles.sep}> / </span>
                        </>
                      )}
                      {formatDuration(turn.duration_ms)}
                    </td>
                    <td className={`${styles.right} num`}>
                      {turn.cost_usd === null ? (
                        <span className="muted">–</span>
                      ) : (
                        formatCost(turn.cost_usd)
                      )}
                    </td>
                    <td>
                      <StatusBadge status={turn.status} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}

function previewTitle(t: GatewayTurn): string | null {
  if (!t.input_preview && !t.output_preview) return null;
  return `Input: ${t.input_preview ?? "–"}\n\nOutput: ${t.output_preview ?? "–"}`;
}
