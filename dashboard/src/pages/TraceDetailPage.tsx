import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import { useTrace } from "../api/queries";
import type { TraceSummary } from "../api/types";
import { ChevronLeftIcon } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { Button, ErrorState } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { buildWaterfall } from "../features/waterfall/layout";
import { SpanInspector } from "../features/waterfall/SpanInspector";
import { Waterfall } from "../features/waterfall/Waterfall";
import {
  traceOrigin,
  formatCost,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatTokens,
} from "../lib/format";
import styles from "./TraceDetailPage.module.css";

export function TraceDetailPage() {
  const { traceId = "" } = useParams();
  const query = useTrace(traceId);
  const [params, setParams] = useSearchParams();
  const selectedSpanId = params.get("span");

  const spans = query.data?.spans;
  const layout = useMemo(() => buildWaterfall(spans ?? []), [spans]);
  const selectedRow = layout.rows.find((r) => r.span.span_id === selectedSpanId);

  const selectSpan = (spanId: string | null) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (spanId === null || spanId === prev.get("span")) next.delete("span");
        else next.set("span", spanId);
        return next;
      },
      { replace: true },
    );
  };

  const back = (
    <Link to="/traces" className={styles.back}>
      <ChevronLeftIcon width={14} height={14} />
      Traces
    </Link>
  );

  if (query.isPending) {
    return (
      <>
        <PageHeader title={back} />
        <p className={styles.loading} aria-busy="true">
          Loading trace…
        </p>
      </>
    );
  }

  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <>
        <PageHeader title={back} />
        <ErrorState
          title={notFound ? "Trace not found" : "Couldn't load this trace"}
          action={
            notFound ? undefined : (
              <Button
                onClick={() => {
                  void query.refetch();
                }}
              >
                Retry
              </Button>
            )
          }
        >
          {notFound ? (
            <>
              No trace with ID <span className="mono">{traceId}</span>.
            </>
          ) : (
            query.error.message
          )}
        </ErrorState>
      </>
    );
  }

  const { trace } = query.data;

  return (
    <div className={styles.page}>
      <PageHeader
        title={
          <span className={styles.titleRow}>
            {back}
            <span className={styles.slash}>/</span>
            <span className={styles.traceName}>{trace.name}</span>
          </span>
        }
      >
        <StatusBadge status={trace.status} />
      </PageHeader>
      <TraceStats trace={trace} />
      <div className={styles.content} data-inspector={selectedRow ? "open" : "closed"}>
        <section className={styles.waterfall} aria-label="Waterfall">
          <Waterfall
            spans={query.data.spans}
            selectedSpanId={selectedSpanId}
            onSelect={(id) => {
              selectSpan(id);
            }}
          />
        </section>
        {selectedRow && (
          <SpanInspector
            key={selectedRow.span.span_id}
            row={selectedRow}
            onClose={() => {
              selectSpan(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

function TraceStats({ trace }: { trace: TraceSummary }) {
  const who = traceOrigin(trace);
  const stats: { label: string; value: string; title?: string; mono?: boolean }[] = [
    { label: "Duration", value: formatDuration(trace.duration_ms) },
    { label: "Spans", value: formatInteger(trace.span_count) },
    { label: "LLM calls", value: formatInteger(trace.llm_calls) },
    {
      label: "Tokens in / out",
      value: `${formatTokens(trace.input_tokens)} / ${formatTokens(trace.output_tokens)}`,
      title: `${formatInteger(trace.input_tokens)} in / ${formatInteger(trace.output_tokens)} out`,
    },
    { label: "Cost", value: formatCost(trace.cost_usd) },
    {
      label: "Source",
      value: `${trace.source === "sdk" ? "SDK" : "Gateway"}${who ? ` · ${who}` : ""}`,
    },
    { label: "Models", value: trace.models.length ? trace.models.join(", ") : "–", mono: true },
    { label: "Started", value: formatDateTime(trace.start_time) },
    { label: "Trace ID", value: trace.trace_id, mono: true },
  ];
  return (
    <dl className={styles.stats}>
      {stats.map((s) => (
        <div key={s.label} className={styles.stat}>
          <dt>{s.label}</dt>
          <dd className={s.mono ? "mono" : "num"} title={s.title ?? s.value}>
            {s.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
