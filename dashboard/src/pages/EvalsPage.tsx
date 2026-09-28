import { useMemo } from "react";
import { ApiError } from "../api/client";
import { useEvalRuns } from "../api/queries";
import { PageHeader } from "../components/PageHeader";
import { ListSkeleton } from "../components/RangeActions";
import { Button, EmptyState, ErrorState } from "../components/States";
import styles from "../features/evals/Evals.module.css";
import { EVAL_COMMAND } from "../features/evals/evals";
import { RunsTable } from "../features/evals/RunsTable";
import pageStyles from "./CostsPage.module.css";
import traceStyles from "./TracesPage.module.css";

export function EvalsPage() {
  const query = useEvalRuns();
  const runs = useMemo(() => query.data?.pages.flatMap((p) => p.runs) ?? [], [query.data]);
  const notReady = query.error instanceof ApiError && query.error.status === 501;

  return (
    <>
      <PageHeader title="Evals" />
      <div className={pageStyles.body}>
        {query.isPending ? (
          <ListSkeleton label="Loading eval runs" />
        ) : !query.data ? (
          notReady ? (
            <EmptyState title="Eval runs aren't available yet">
              This LucentPad server doesn&apos;t store eval runs.
            </EmptyState>
          ) : (
            <ErrorState
              title="Couldn't load eval runs"
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
          )
        ) : runs.length === 0 ? (
          <EmptyState title="No eval runs yet">
            <p>Run your saved cases against the baseline; each run shows up here.</p>
            <code className={`mono ${styles.command}`}>{EVAL_COMMAND}</code>
          </EmptyState>
        ) : (
          <>
            <RunsTable runs={runs} />
            {query.isFetchNextPageError && (
              <p className={traceStyles.pageError} role="alert">
                Couldn&apos;t load more runs: {query.error.message}
              </p>
            )}
            {query.hasNextPage && (
              <div className={traceStyles.more}>
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
