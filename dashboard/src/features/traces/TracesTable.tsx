import { useLayoutEffect, useRef, type RefObject } from "react";
import { Link, useNavigate } from "react-router";
import type { TraceOrder, TraceSummary } from "../../api/types";
import { ArrowDownIcon, ArrowUpIcon } from "../../components/icons";
import { StatusBadge } from "../../components/StatusBadge";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatIsoUtc,
  formatRelative,
  formatTokens,
  previewLine,
  traceOrigin,
} from "../../lib/format";
import { useNow } from "../../lib/useNow";
import styles from "./TracesTable.module.css";

interface TracesTableProps {
  traces: TraceSummary[];
  order: TraceOrder;
  onToggleOrder: () => void;
  /** Trace ids that just arrived by live polling (briefly highlighted). */
  fresh?: ReadonlySet<string>;
}

function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
  }
  return null;
}

/**
 * Keep what the user is looking at in place when rows are prepended above it: if the list
 * is scrolled, shift the scroll position by the height the new rows added.
 */
function useScrollAnchor(tbody: RefObject<HTMLTableSectionElement | null>, firstId: string) {
  const anchor = useRef<{ id: string; top: number } | null>(null);
  useLayoutEffect(() => {
    const body = tbody.current;
    if (!body) return;
    const prev = anchor.current;
    if (prev && prev.id !== firstId) {
      const row = body.querySelector<HTMLElement>(`tr[data-trace-id="${prev.id}"]`);
      const scroller = scrollParent(body);
      if (row && scroller && scroller.scrollTop > 0) {
        scroller.scrollTop += row.offsetTop - prev.top;
      }
    }
    const first = body.querySelector<HTMLElement>(`tr[data-trace-id="${firstId}"]`);
    anchor.current = first ? { id: firstId, top: first.offsetTop } : null;
  }, [tbody, firstId]);
}

export function TracesTable({ traces, order, onToggleOrder, fresh }: TracesTableProps) {
  const navigate = useNavigate();
  const now = useNow();
  const tbody = useRef<HTMLTableSectionElement>(null);
  useScrollAnchor(tbody, traces[0]?.trace_id ?? "");
  const SortIcon = order === "desc" ? ArrowDownIcon : ArrowUpIcon;

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col" className={styles.colName}>
              Name
            </th>
            <th scope="col" className={styles.colSource}>
              Source
            </th>
            <th scope="col" className={styles.colStatus}>
              Status
            </th>
            <th
              scope="col"
              className={styles.colStarted}
              aria-sort={order === "desc" ? "descending" : "ascending"}
            >
              <button
                type="button"
                className={styles.sort}
                onClick={onToggleOrder}
                title={
                  order === "desc"
                    ? "Newest first; click for oldest first"
                    : "Oldest first; click for newest first"
                }
              >
                Started
                <SortIcon width={12} height={12} className={styles.sortIcon} />
              </button>
            </th>
            <th scope="col" className={styles.colPreview}>
              Input → Output
            </th>
            <th scope="col" className={`${styles.right} ${styles.colDuration}`}>
              Duration
            </th>
            <th scope="col" className={`${styles.right} ${styles.optional} ${styles.colCalls}`}>
              LLM calls
            </th>
            <th scope="col" className={`${styles.right} ${styles.colTokens}`}>
              Tokens in / out
            </th>
            <th scope="col" className={`${styles.right} ${styles.colCost}`}>
              Cost
            </th>
            <th scope="col" className={`${styles.optional} ${styles.colModels}`}>
              Models
            </th>
          </tr>
        </thead>
        <tbody ref={tbody}>
          {traces.map((t) => {
            const href = `/traces/${t.trace_id}`;
            const origin = traceOrigin(t);
            const preview = previewLine(t.input_preview, t.output_preview);
            return (
              <tr
                key={t.trace_id}
                className={styles.row}
                data-trace-id={t.trace_id}
                data-fresh={fresh?.has(t.trace_id) ? "true" : undefined}
                onClick={(e) => {
                  if (e.target instanceof Element && e.target.closest("a")) return;
                  void navigate(href);
                }}
              >
                <td className={styles.clip}>
                  <Link to={href} className={styles.name} title={t.name}>
                    {t.name}
                  </Link>
                </td>
                <td className={styles.clip}>
                  <span className={styles.source}>
                    <span className={styles.sourceKind}>
                      {t.source === "sdk" ? "SDK" : "Gateway"}
                    </span>
                    {origin && <span className="muted">{origin}</span>}
                  </span>
                </td>
                <td>
                  <StatusBadge status={t.status} />
                </td>
                <td
                  className="muted num"
                  title={`${formatRelative(t.start_time, now)} · ${formatIsoUtc(t.start_time)}`}
                >
                  <time dateTime={t.start_time}>{formatDateTime(t.start_time)}</time>
                </td>
                <td
                  className={`${styles.clip} ${styles.preview}`}
                  title={previewTitle(t) ?? undefined}
                  data-testid="preview"
                >
                  {preview ?? <span className="muted">–</span>}
                </td>
                <td className={`${styles.right} num`}>{formatDuration(t.duration_ms)}</td>
                <td className={`${styles.right} ${styles.optional} num`}>{t.llm_calls}</td>
                <td className={`${styles.right} num`}>
                  {formatTokens(t.input_tokens)}
                  <span className={styles.sep}> / </span>
                  {formatTokens(t.output_tokens)}
                </td>
                <td className={`${styles.right} num`}>{formatCost(t.cost_usd)}</td>
                <td className={`${styles.clip} ${styles.optional}`}>
                  <Models models={t.models} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Full previews for the hover tooltip, line breaks kept. */
function previewTitle(t: TraceSummary): string | null {
  if (!t.input_preview && !t.output_preview) return null;
  return `Input: ${t.input_preview ?? "–"}\n\nOutput: ${t.output_preview ?? "–"}`;
}

function Models({ models }: { models: string[] }) {
  const [first, ...rest] = models;
  if (first === undefined) return <span className="muted">–</span>;
  return (
    <span className={styles.models} title={models.join(", ")}>
      <span className={`mono ${styles.model}`}>{first}</span>
      {rest.length > 0 && <span className={styles.moreModels}>+{rest.length}</span>}
    </span>
  );
}
