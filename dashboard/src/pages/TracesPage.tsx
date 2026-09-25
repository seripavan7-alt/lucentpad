import { Link, useNavigate, useSearchParams } from "react-router";
import { useTraces, type TraceFilters } from "../api/queries";
import {
  SPAN_SOURCES,
  SPAN_STATUSES,
  type SpanSource,
  type SpanStatus,
  type TraceSummary,
} from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl, type SegmentOption } from "../components/SegmentedControl";
import { Button, EmptyState, ErrorState } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import {
  traceOrigin,
  formatCost,
  formatDateTime,
  formatDuration,
  formatRelative,
  formatTokens,
} from "../lib/format";
import { useNow } from "../lib/useNow";
import styles from "./TracesPage.module.css";

type All = "all";

const SOURCE_OPTIONS: SegmentOption<SpanSource | All>[] = [
  { value: "all", label: "All" },
  { value: "sdk", label: "SDK" },
  { value: "gateway", label: "Gateway" },
];

const STATUS_OPTIONS: SegmentOption<SpanStatus | All>[] = [
  { value: "all", label: "All" },
  { value: "ok", label: "OK" },
  { value: "error", label: "Error" },
  { value: "blocked", label: "Blocked" },
];

function pick<T extends string>(allowed: readonly T[], value: string | null): T | undefined {
  return (allowed as readonly (string | null)[]).includes(value) ? (value as T) : undefined;
}

type FilterPatch = Partial<Record<keyof TraceFilters, string>>;

/** Filters live in the URL (?source=&status=) so views are linkable; "all" clears one. */
function useTraceFilters(): [TraceFilters, (patch: FilterPatch) => void] {
  const [params, setParams] = useSearchParams();
  const filters: TraceFilters = {
    source: pick(SPAN_SOURCES, params.get("source")),
    status: pick(SPAN_STATUSES, params.get("status")),
  };
  const setFilter = (patch: FilterPatch) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [key, value] of Object.entries(patch)) {
        if (value === "all") next.delete(key);
        else next.set(key, value);
      }
      return next;
    });
  };
  return [filters, setFilter];
}

export function TracesPage() {
  const [filters, setFilter] = useTraceFilters();
  const query = useTraces(filters);
  const traces = query.data?.pages.flatMap((page) => page.traces) ?? [];
  const filtered = filters.source !== undefined || filters.status !== undefined;

  return (
    <>
      <PageHeader
        title="Traces"
        actions={
          <>
            <SegmentedControl
              label="Source"
              options={SOURCE_OPTIONS}
              value={filters.source ?? "all"}
              onChange={(v) => {
                setFilter({ source: v });
              }}
            />
            <SegmentedControl
              label="Status"
              options={STATUS_OPTIONS}
              value={filters.status ?? "all"}
              onChange={(v) => {
                setFilter({ status: v });
              }}
            />
          </>
        }
      />
      <div className={styles.body}>
        {query.isPending ? (
          <TableSkeleton />
        ) : query.data === undefined ? (
          <ErrorState
            title="Couldn't load traces"
            action={
              <Button
                onClick={() => {
                  void query.refetch();
                }}
              >
                Retry
              </Button>
            }
          >
            {query.error.message}
          </ErrorState>
        ) : traces.length === 0 ? (
          filtered ? (
            <EmptyState
              title="No traces match these filters"
              action={
                <Button
                  onClick={() => {
                    setFilter({ source: "all", status: "all" });
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          ) : (
            <EmptyState title="No traces yet">
              Run an agent with the Prism SDK or point a coding assistant at the gateway, and its
              traces will show up here.
            </EmptyState>
          )
        ) : (
          <>
            <TracesTable traces={traces} />
            {query.isFetchNextPageError && (
              <p className={styles.pageError} role="alert">
                Couldn&apos;t load more traces: {query.error.message}
              </p>
            )}
            {query.hasNextPage && (
              <div className={styles.more}>
                <Button
                  onClick={() => {
                    void query.fetchNextPage();
                  }}
                  disabled={query.isFetchingNextPage}
                >
                  {query.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

function TracesTable({ traces }: { traces: TraceSummary[] }) {
  const navigate = useNavigate();
  const now = useNow();
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Source</th>
            <th scope="col">Status</th>
            <th scope="col">Started</th>
            <th scope="col" className={styles.right}>
              Duration
            </th>
            <th scope="col" className={styles.right}>
              LLM calls
            </th>
            <th scope="col" className={styles.right}>
              Tokens in / out
            </th>
            <th scope="col" className={styles.right}>
              Cost
            </th>
            <th scope="col">Models</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => {
            const href = `/traces/${t.trace_id}`;
            return (
              <tr
                key={t.trace_id}
                className={styles.row}
                onClick={(e) => {
                  if (e.target instanceof Element && e.target.closest("a")) return;
                  void navigate(href);
                }}
              >
                <td className={styles.nameCell}>
                  <Link to={href} className={styles.name}>
                    {t.name}
                  </Link>
                </td>
                <td>
                  <span className={styles.source}>
                    <span className={styles.sourceKind}>
                      {t.source === "sdk" ? "SDK" : "Gateway"}
                    </span>
                    {traceOrigin(t) && <span className="muted">{traceOrigin(t)}</span>}
                  </span>
                </td>
                <td>
                  <StatusBadge status={t.status} />
                </td>
                <td className="muted num" title={formatDateTime(t.start_time)}>
                  <time dateTime={t.start_time}>{formatRelative(t.start_time, now)}</time>
                </td>
                <td className={`${styles.right} num`}>{formatDuration(t.duration_ms)}</td>
                <td className={`${styles.right} num`}>{t.llm_calls}</td>
                <td className={`${styles.right} num`}>
                  {formatTokens(t.input_tokens)}
                  <span className={styles.sep}> / </span>
                  {formatTokens(t.output_tokens)}
                </td>
                <td className={`${styles.right} num`}>{formatCost(t.cost_usd)}</td>
                <td>
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

function Models({ models }: { models: string[] }) {
  const [first, ...rest] = models;
  if (first === undefined) return <span className="muted">–</span>;
  return (
    <span className={styles.models} title={models.join(", ")}>
      <span className="mono">{first}</span>
      {rest.length > 0 && <span className={styles.moreModels}>+{rest.length}</span>}
    </span>
  );
}

function TableSkeleton() {
  return (
    <div className={styles.skeleton} aria-busy="true" aria-label="Loading traces">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className={styles.skeletonRow} />
      ))}
    </div>
  );
}
