import { useLayoutEffect, useRef, type RefObject } from "react";
import { Link, useNavigate } from "react-router";
import type { TraceOrder, TraceSort, TraceSummary } from "../../api/types";
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
import { FIRST_ORDER } from "./view";

interface TracesTableProps {
  traces: TraceSummary[];
  sort: TraceSort;
  order: TraceOrder;
  /** A sortable header was clicked (the caller flips or switches; see `nextSort`). */
  onSort: (column: TraceSort) => void;
  /** Trace ids that just arrived by live polling (briefly highlighted). */
  fresh?: ReadonlySet<string>;
  /** The rows are the previous view's, shown while a new sort or filter loads. */
  stale?: boolean;
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

/** What each direction means per column, for the header's tooltip. */
const SORT_PHRASES: Record<TraceSort, Record<TraceOrder, string>> = {
  started: { desc: "Newest first", asc: "Oldest first" },
  duration: { desc: "Longest first", asc: "Shortest first" },
  cost: { desc: "Most expensive first", asc: "Cheapest first" },
  name: { asc: "Name A→Z", desc: "Name Z→A" },
  source: { asc: "Source A→Z", desc: "Source Z→A" },
};

interface SortHeaderProps {
  column: TraceSort;
  label: string;
  className: string | undefined;
  /** Right-aligned (numeric) column: the arrow goes before the label so labels line up. */
  right?: boolean;
  sort: TraceSort;
  order: TraceOrder;
  onSort: (column: TraceSort) => void;
}

function SortHeader({ column, label, className, right, sort, order, onSort }: SortHeaderProps) {
  const active = sort === column;
  // Inactive columns preview the direction a first click would sort in.
  const shown = active ? order : FIRST_ORDER[column];
  const Icon = shown === "desc" ? ArrowDownIcon : ArrowUpIcon;
  const phrases = SORT_PHRASES[column];
  const title = active
    ? `${phrases[order]}; click for ${phrases[order === "desc" ? "asc" : "desc"].toLowerCase()}`
    : `Sort: ${phrases[shown].toLowerCase()}`;
  const icon = (
    <Icon
      width={12}
      height={12}
      className={styles.sortIcon}
      data-active={active ? "true" : undefined}
      aria-hidden="true"
    />
  );
  return (
    <th
      scope="col"
      className={`${right ? (styles.right ?? "") : ""} ${className ?? ""}`}
      aria-sort={active ? (order === "desc" ? "descending" : "ascending") : undefined}
    >
      <button
        type="button"
        className={`${styles.sort ?? ""} ${right ? (styles.sortRight ?? "") : ""}`}
        data-active={active ? "true" : undefined}
        onClick={() => {
          onSort(column);
        }}
        title={title}
      >
        {right && icon}
        {label}
        {!right && icon}
      </button>
    </th>
  );
}

export function TracesTable({ traces, sort, order, onSort, fresh, stale }: TracesTableProps) {
  const navigate = useNavigate();
  const now = useNow();
  const tbody = useRef<HTMLTableSectionElement>(null);
  useScrollAnchor(tbody, traces[0]?.trace_id ?? "");
  const sortProps = { sort, order, onSort };

  return (
    <div
      className={styles.tableWrap}
      aria-busy={stale ? "true" : undefined}
      data-stale={stale ? "true" : undefined}
    >
      <table className={styles.table}>
        <thead>
          <tr>
            <SortHeader column="name" label="Name" className={styles.colName} {...sortProps} />
            <SortHeader
              column="source"
              label="Source"
              className={styles.colSource}
              {...sortProps}
            />
            <th scope="col" className={styles.colStatus}>
              Status
            </th>
            <SortHeader
              column="started"
              label="Started"
              className={styles.colStarted}
              {...sortProps}
            />
            <th scope="col" className={styles.colPreview}>
              Input → Output
            </th>
            <SortHeader
              column="duration"
              label="Duration"
              className={styles.colDuration}
              right
              {...sortProps}
            />
            <th scope="col" className={`${styles.right} ${styles.optional} ${styles.colCalls}`}>
              LLM calls
            </th>
            <th scope="col" className={`${styles.right} ${styles.colTokens}`}>
              Tokens in / out
            </th>
            <SortHeader
              column="cost"
              label="Cost"
              className={styles.colCost}
              right
              {...sortProps}
            />
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
