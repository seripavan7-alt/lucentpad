import { useId, useState } from "react";
import { useLiveTraces, useTraceFacets, useTraces } from "../api/queries";
import { FilterIcon, RefreshIcon } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl, type SegmentOption } from "../components/SegmentedControl";
import { Button, EmptyState, ErrorState } from "../components/States";
import { FilterPanel } from "../features/traces/FilterPanel";
import { TracesTable } from "../features/traces/TracesTable";
import {
  activeFilterCount,
  NO_FILTERS,
  RANGES,
  rangeInfo,
  rangePhrase,
  useTracesView,
  type RangeId,
} from "../features/traces/view";
import styles from "./TracesPage.module.css";

const RANGE_OPTIONS: SegmentOption<RangeId>[] = RANGES.map((r) => ({ value: r.id, label: r.id }));

/** The next wider preset the empty state offers: 24 hours, or 30 days from 24h and up. */
function widerRange(range: RangeId): RangeId | null {
  const ms = rangeInfo(range).ms;
  if (ms < rangeInfo("24h").ms) return "24h";
  if (ms < rangeInfo("30d").ms) return "30d";
  return null;
}

export function TracesPage() {
  const [view, update] = useTracesView();
  const query = useTraces(view);
  const facets = useTraceFacets(view);
  const fresh = useLiveTraces(view, query.isSuccess);
  const [panelOpen, setPanelOpen] = useState(false);
  const panelId = useId();

  const traces = query.data?.pages.flatMap((page) => page.traces) ?? [];
  const active = activeFilterCount(view.filters);
  const wider = widerRange(view.range);

  const refresh = () => {
    void query.refetch();
    void facets.refetch();
  };

  return (
    <>
      <PageHeader
        title="Traces"
        actions={
          <>
            <button
              type="button"
              className={styles.filtersButton}
              aria-expanded={panelOpen}
              aria-controls={panelId}
              onClick={() => {
                setPanelOpen((v) => !v);
              }}
            >
              <FilterIcon width={14} height={14} />
              Filters
              {active > 0 && <span className={styles.filtersCount}>{active}</span>}
            </button>
            <SegmentedControl
              label="Time range"
              options={RANGE_OPTIONS}
              value={view.range}
              onChange={(range) => {
                update({ range });
              }}
            />
            <button
              type="button"
              className={styles.iconButton}
              aria-label="Refresh"
              title="Refresh"
              onClick={refresh}
              data-spinning={query.isRefetching && !query.isFetchingNextPage ? "true" : undefined}
            >
              <RefreshIcon width={14} height={14} />
            </button>
          </>
        }
      />
      <div className={styles.layout}>
        <FilterPanel
          id={panelId}
          open={panelOpen}
          facets={facets.data}
          loading={facets.isPending}
          error={facets.isError}
          filters={view.filters}
          onChange={(filters) => {
            update({ filters });
          }}
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
            <EmptyState
              title={
                active > 0
                  ? `No traces match these filters in ${rangePhrase(view.range)}`
                  : `No traces in ${rangePhrase(view.range)}`
              }
              action={
                <span className={styles.emptyActions}>
                  {wider && (
                    <Button
                      onClick={() => {
                        update({ range: wider });
                      }}
                    >
                      Show last {rangeInfo(wider).phrase}
                    </Button>
                  )}
                  {active > 0 && (
                    <Button
                      onClick={() => {
                        update({ filters: NO_FILTERS });
                      }}
                    >
                      Clear filters
                    </Button>
                  )}
                </span>
              }
            >
              {active === 0 &&
                "Run an agent with the LucentPad SDK or point a coding assistant at the gateway, and its traces will show up here."}
            </EmptyState>
          ) : (
            <>
              <TracesTable
                traces={traces}
                order={view.order}
                fresh={fresh}
                onToggleOrder={() => {
                  update({ order: view.order === "desc" ? "asc" : "desc" });
                }}
              />
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
      </div>
    </>
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
