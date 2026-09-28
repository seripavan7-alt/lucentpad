import { useMemo } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { useEvalRuns } from "../api/queries";
import { PageHeader } from "../components/PageHeader";
import { ListSkeleton } from "../components/RangeActions";
import { Button, EmptyState, ErrorState } from "../components/States";
import styles from "../features/evals/Evals.module.css";
import { EVAL_COMMAND } from "../features/evals/evals";
import { RunList } from "../features/evals/RunList";
import { RunPanel } from "../features/evals/RunPanel";
import traceStyles from "./TracesPage.module.css";

/**
 * Runs on the left, the selected run and its cases on the right (`/evals/:runId`; `/evals`
 * shows the newest run). The selected row and the run panel share the accent marker, so it's
 * clear which run the cases belong to. On narrow screens the list and the run take turns.
 */
export function EvalsPage() {
  const { runId } = useParams();
  const query = useEvalRuns();
  const runs = useMemo(() => query.data?.pages.flatMap((p) => p.runs) ?? [], [query.data]);
  const notReady = query.error instanceof ApiError && query.error.status === 501;
  const selectedId = runId ?? runs[0]?.id ?? null;
  const index = runs.findIndex((r) => r.id === selectedId);
  const position =
    index < 0
      ? null
      : `${index + 1} of ${runs.length}${query.hasNextPage ? "+" : ""}` +
        (index === 0 ? " · newest" : "");

  let body;
  if (query.isPending) {
    body = <ListSkeleton label="Loading eval runs" />;
  } else if (!query.data) {
    body = notReady ? (
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
    );
  } else if (runs.length === 0 && !runId) {
    body = (
      <EmptyState title="No eval runs yet">
        <p>Run your saved cases against the baseline; each run shows up here.</p>
        <code className={`mono ${styles.command}`}>{EVAL_COMMAND}</code>
      </EmptyState>
    );
  } else {
    body = (
      <div className={styles.split} data-has-run={runId ? "true" : "false"}>
        <aside className={styles.runsPane} aria-label="Runs">
          <div className={styles.paneHead}>
            <span>Runs</span>
            <span className={styles.count}>
              {runs.length}
              {query.hasNextPage ? "+" : ""}
            </span>
          </div>
          <RunList runs={runs} selectedId={selectedId} />
          {query.isFetchNextPageError && (
            <p className={traceStyles.pageError} role="alert">
              Couldn&apos;t load more runs: {query.error.message}
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
        </aside>
        <Link to="/evals" className={styles.backLink}>
          ← All runs
        </Link>
        <section className={styles.panel} aria-label="Selected eval run">
          {selectedId && <RunPanel key={selectedId} runId={selectedId} position={position} />}
        </section>
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Evals" />
      {body}
    </>
  );
}
