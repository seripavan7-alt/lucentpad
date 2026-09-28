import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { useEvalRun } from "../api/queries";
import type { EvalRun } from "../api/types";
import table from "../components/DataTable.module.css";
import { PageHeader } from "../components/PageHeader";
import { ListSkeleton } from "../components/RangeActions";
import { StatTiles } from "../components/StatTiles";
import { Button, EmptyState, ErrorState } from "../components/States";
import { CaseTable } from "../features/evals/CaseTable";
import { EvalStatusBadge } from "../features/evals/EvalStatusBadge";
import styles from "../features/evals/Evals.module.css";
import { caseChange, costDelta, shortSha } from "../features/evals/evals";
import { formatCost, formatDateTime, formatDuration, formatInteger } from "../lib/format";
import pageStyles from "./CostsPage.module.css";

function RunMeta({ run }: { run: EvalRun }) {
  const sha = shortSha(run.git_sha);
  return (
    <p className={styles.meta}>
      <span className={styles.metaItem}>
        <span className={styles.metaLabel}>Started</span>
        <time dateTime={run.started_at} className="num">
          {formatDateTime(run.started_at)}
        </time>
      </span>
      <span className={styles.metaItem}>
        <span className={styles.metaLabel}>Took</span>
        <span className="num">{formatDuration(run.duration_ms)}</span>
      </span>
      {run.model && (
        <span className={styles.metaItem}>
          <span className={styles.metaLabel}>Model</span>
          <span className="mono">{run.model}</span>
        </span>
      )}
      {(run.git_ref ?? sha) && (
        <span className={styles.metaItem}>
          <span className={styles.metaLabel}>Commit</span>
          {run.git_ref}
          {run.git_ref && sha && " · "}
          {sha && <span className="mono">{sha}</span>}
        </span>
      )}
      {run.ci_url && (
        <a href={run.ci_url} target="_blank" rel="noreferrer" className={table.link}>
          CI run
        </a>
      )}
    </p>
  );
}

function runTiles(run: EvalRun) {
  const passed = run.cases.filter((c) => c.passed).length;
  const regressions = run.cases.filter((c) => caseChange(c) === "regressed").length;
  const fixed = run.cases.filter((c) => caseChange(c) === "fixed").length;
  const added = run.cases.filter((c) => caseChange(c) === "new").length;
  const delta = costDelta(run.cost_usd, run.baseline_cost_usd);
  return [
    {
      label: "Cases passed",
      value: `${formatInteger(passed)} of ${formatInteger(run.cases.length)}`,
      meta: `${formatInteger(run.cases.length - passed)} failed${added ? ` · ${formatInteger(added)} new` : ""}`,
    },
    {
      label: "Regressions",
      value: formatInteger(regressions),
      meta: regressions
        ? "passed in the baseline, fail now"
        : fixed
          ? `${formatInteger(fixed)} fixed since the baseline`
          : "none against the baseline",
      empty: regressions === 0,
    },
    {
      label: "Cost",
      value: run.cost_usd === null ? "–" : formatCost(run.cost_usd),
      meta:
        run.baseline_cost_usd === null
          ? "no baseline cost"
          : `${delta ?? ""} vs baseline ${formatCost(run.baseline_cost_usd)}`.trim(),
      empty: run.cost_usd === null,
    },
  ];
}

export function EvalRunPage() {
  const { runId = "" } = useParams();
  const query = useEvalRun(runId);
  const run = query.data;

  const title = (
    <>
      <Link to="/evals" className={styles.back}>
        Evals
      </Link>
      <span className={styles.crumbSep}> / </span>
      {run ? run.suite : "Run"}
    </>
  );

  return (
    <>
      <PageHeader title={title}>{run && <EvalStatusBadge status={run.status} />}</PageHeader>
      <div className={pageStyles.body}>
        {query.isPending ? (
          <ListSkeleton label="Loading eval run" />
        ) : !run ? (
          query.error instanceof ApiError && query.error.status === 404 ? (
            <EmptyState
              title="This eval run doesn't exist"
              action={<Link to="/evals">All runs</Link>}
            />
          ) : (
            <ErrorState
              title="Couldn't load the eval run"
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
        ) : (
          <>
            <RunMeta run={run} />
            <StatTiles label="Run summary" tiles={runTiles(run)} />
            <div className={table.sectionHead}>
              <h2 className={table.sectionTitle}>
                Cases
                <span className={table.sectionNote}>regressions first</span>
              </h2>
            </div>
            {run.cases.length === 0 ? (
              <EmptyState title="This run has no cases" />
            ) : (
              <CaseTable cases={run.cases} />
            )}
          </>
        )}
      </div>
    </>
  );
}
