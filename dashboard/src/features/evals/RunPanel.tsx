import { Link } from "react-router";
import { ApiError } from "../../api/client";
import { useEvalRun } from "../../api/queries";
import type { EvalRun } from "../../api/types";
import { ListSkeleton } from "../../components/RangeActions";
import { StatTiles } from "../../components/StatTiles";
import { Button, EmptyState, ErrorState } from "../../components/States";
import { formatCost, formatDateTime, formatDuration, formatInteger } from "../../lib/format";
import { CaseList } from "./CaseList";
import { EvalStatusBadge } from "./EvalStatusBadge";
import styles from "./Evals.module.css";
import { caseChange, costDelta, orderCases, shortSha } from "./evals";

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
        ? "passed before, fail now"
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

/** One segment per case, in the same order as the case list below it. */
function CaseBar({ run }: { run: EvalRun }) {
  const cases = orderCases(run.cases);
  return (
    <div className={styles.caseBar} aria-hidden="true">
      {cases.map((c) => (
        <i
          key={c.case}
          data-kind={caseChange(c) === "regressed" ? "regressed" : c.passed ? "passed" : "failed"}
          title={c.case}
        />
      ))}
    </div>
  );
}

function RunHeader({ run, position }: { run: EvalRun; position: string | null }) {
  const sha = shortSha(run.git_sha);
  return (
    <header className={styles.panelHead}>
      <p className={styles.eyebrow}>
        Selected run{position && <span className={styles.position}> · {position}</span>}
      </p>
      <div className={styles.panelTitleRow}>
        <h2 className={styles.panelTitle}>
          {run.suite}
          <span className={styles.panelTime}>
            <time dateTime={run.started_at} className="num">
              {formatDateTime(run.started_at)}
            </time>
          </span>
        </h2>
        <EvalStatusBadge status={run.status} />
      </div>
      <p className={styles.chips}>
        <span className={styles.chip}>
          <span className={styles.chipLabel}>Took</span>
          <span className="num">{formatDuration(run.duration_ms)}</span>
        </span>
        {run.model && (
          <span className={styles.chip}>
            <span className={styles.chipLabel}>Model</span>
            <span className="mono">{run.model}</span>
          </span>
        )}
        {(run.git_ref ?? sha) && (
          <span className={styles.chip}>
            <span className={styles.chipLabel}>Commit</span>
            {run.git_ref}
            {run.git_ref && sha && " · "}
            {sha && <span className="mono">{sha}</span>}
          </span>
        )}
        {run.ci_url && (
          <a href={run.ci_url} target="_blank" rel="noreferrer" className={styles.chipLink}>
            CI run ↗
          </a>
        )}
      </p>
    </header>
  );
}

export function RunPanel({ runId, position }: { runId: string; position: string | null }) {
  const query = useEvalRun(runId);
  const run = query.data;

  if (query.isPending) return <ListSkeleton label="Loading eval run" />;
  if (!run) {
    return query.error instanceof ApiError && query.error.status === 404 ? (
      <EmptyState title="This eval run doesn't exist" action={<Link to="/evals">All runs</Link>} />
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
    );
  }

  const label = `Cases in the ${run.suite} run of ${formatDateTime(run.started_at)}`;
  return (
    <>
      <RunHeader run={run} position={position} />
      <CaseBar run={run} />
      <div className={styles.panelTiles}>
        <StatTiles label="Run summary" tiles={runTiles(run)} />
      </div>
      <div className={styles.casesHead}>
        <h3 className={styles.casesTitle}>
          Cases in this run
          <span className={styles.count}>{run.cases.length}</span>
        </h3>
        <span className={styles.casesNote}>regressions first · click a case for details</span>
      </div>
      {run.cases.length === 0 ? (
        <EmptyState title="This run has no cases" />
      ) : (
        <CaseList cases={run.cases} label={label} />
      )}
    </>
  );
}
