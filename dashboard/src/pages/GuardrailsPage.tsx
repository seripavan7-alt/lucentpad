import { useMemo } from "react";
import { ApiError } from "../api/client";
import {
  useGuardrailEvents,
  useGuardrailRules,
  useGuardrailSummary,
  useLiveGuardrailEvents,
} from "../api/queries";
import type { GuardrailEventKind, GuardrailSummary } from "../api/types";
import table from "../components/DataTable.module.css";
import { PageHeader } from "../components/PageHeader";
import { ListSkeleton, RangeActions } from "../components/RangeActions";
import { SegmentedControl, type SegmentOption } from "../components/SegmentedControl";
import { StatTiles } from "../components/StatTiles";
import { Button, EmptyState, ErrorState } from "../components/States";
import { EventsTable } from "../features/guardrails/EventsTable";
import { RulesTable } from "../features/guardrails/RulesTable";
import { redactionCount, useGuardrailsView } from "../features/guardrails/view";
import { rangeInfo, rangePhrase, widerRange } from "../features/traces/view";
import { formatInteger } from "../lib/format";
import styles from "./CostsPage.module.css";
import traceStyles from "./TracesPage.module.css";

type KindOption = GuardrailEventKind | "all";
const KIND_OPTIONS: SegmentOption<KindOption>[] = [
  { value: "all", label: "All" },
  { value: "block", label: "Blocks" },
  { value: "redaction", label: "Redactions" },
  { value: "budget", label: "Budget" },
];

const KIND_NOUNS: Record<KindOption, string> = {
  all: "guardrail events",
  block: "blocks",
  redaction: "redactions",
  budget: "budget alerts",
};

const notImplemented = (error: Error | null) => error instanceof ApiError && error.status === 501;

const plural = (n: number, one: string, many: string) =>
  `${formatInteger(n)} ${n === 1 ? one : many}`;

function summaryTiles(summary: GuardrailSummary | undefined) {
  const blocks = summary?.blocks.reduce((n, b) => n + b.blocks, 0) ?? 0;
  const redacted = summary?.redactions.reduce((n, r) => n + r.count, 0) ?? 0;
  const budget = summary?.budget_alerts ?? 0;
  const kinds = summary?.redactions
    .slice(0, 3)
    .map((r) => redactionCount(r.value, r.count))
    .join(" · ");
  return [
    {
      label: "Blocks",
      value: formatInteger(blocks),
      meta: blocks
        ? `by ${plural(summary?.blocks.length ?? 0, "rule", "rules")}`
        : "nothing blocked",
      empty: blocks === 0,
    },
    {
      label: "Values redacted",
      value: formatInteger(redacted),
      meta: redacted > 0 ? kinds : "nothing redacted",
      empty: redacted === 0,
    },
    {
      label: "Budget alerts",
      value: formatInteger(budget),
      meta: budget > 0 ? "runs or sessions over budget" : "no budget exceeded",
      empty: budget === 0,
    },
  ];
}

export function GuardrailsPage() {
  const [view, update] = useGuardrailsView();
  const rules = useGuardrailRules();
  const summary = useGuardrailSummary(view.range);
  const eventsQuery = useGuardrailEvents(view);
  const fresh = useLiveGuardrailEvents(view, eventsQuery.isSuccess);
  const wider = widerRange(view.range);

  const events = useMemo(
    () => eventsQuery.data?.pages.flatMap((p) => p.events) ?? [],
    [eventsQuery.data],
  );

  const refresh = () => {
    void rules.refetch();
    void summary.refetch();
    void eventsQuery.refetch();
  };

  const widerButton = wider && (
    <Button
      onClick={() => {
        update({ range: wider });
      }}
    >
      Show last {rangeInfo(wider).phrase}
    </Button>
  );

  const kindNoun = KIND_NOUNS[view.kind ?? "all"];

  return (
    <>
      <PageHeader
        title="Guardrails"
        actions={
          <RangeActions
            range={view.range}
            onRange={(range) => {
              update({ range });
            }}
            onRefresh={refresh}
            refreshing={eventsQuery.isRefetching && !eventsQuery.isFetchingNextPage}
          />
        }
      />
      <div className={styles.body}>
        {!notImplemented(summary.error) && (
          <StatTiles
            label="Counts for the range"
            tiles={summaryTiles(summary.data)}
            loading={summary.isPending}
            columns={4}
          />
        )}

        <section aria-labelledby="rules-title">
          <div className={table.sectionHead}>
            <h2 id="rules-title" className={table.sectionTitle}>
              Rules
              {rules.data && (
                <span className={table.sectionNote}>
                  {rules.data.source === "built-in" ? "built-in rules" : rules.data.source}
                  {" · version "}
                  <span className="mono">{rules.data.version.slice(0, 12)}</span>
                </span>
              )}
            </h2>
          </div>
          {rules.isPending ? (
            <ListSkeleton label="Loading rules" rows={2} />
          ) : notImplemented(rules.error) ? (
            <EmptyState title="Rules aren't loaded">
              This server isn&apos;t serving guardrail rules, so nothing is blocked. Redaction still
              runs.
            </EmptyState>
          ) : !rules.data ? (
            <ErrorState
              title="Couldn't load rules"
              action={
                <Button
                  onClick={() => {
                    void rules.refetch();
                  }}
                >
                  Retry
                </Button>
              }
            >
              {rules.error.message}
            </ErrorState>
          ) : rules.data.rules.length === 0 && !summary.data?.blocks.length ? (
            <EmptyState title="No blocking rules">
              Add prompt or tool rules to the rules file and they&apos;ll show up here.
            </EmptyState>
          ) : (
            <RulesTable rules={rules.data.rules} summary={summary.data} />
          )}
        </section>

        <section aria-labelledby="events-title">
          <div className={table.sectionHead}>
            <h2 id="events-title" className={table.sectionTitle}>
              Events
              <span className={table.sectionNote}>live</span>
            </h2>
            <SegmentedControl
              label="Event kind"
              options={KIND_OPTIONS}
              value={view.kind ?? "all"}
              onChange={(value) => {
                update({ kind: value === "all" ? null : value });
              }}
            />
          </div>
          {eventsQuery.isPending ? (
            <ListSkeleton label="Loading guardrail events" />
          ) : notImplemented(eventsQuery.error) && !eventsQuery.data ? (
            <EmptyState title="Guardrail events aren't available yet">
              This LucentPad server doesn&apos;t answer guardrail event queries.
            </EmptyState>
          ) : !eventsQuery.data ? (
            <ErrorState
              title="Couldn't load guardrail events"
              action={
                <Button
                  onClick={() => {
                    void eventsQuery.refetch();
                  }}
                >
                  Retry
                </Button>
              }
            >
              {eventsQuery.error.message}
            </ErrorState>
          ) : events.length === 0 ? (
            <EmptyState
              title={`No ${kindNoun} in ${rangePhrase(view.range)}`}
              action={
                <span className={traceStyles.emptyActions}>
                  {view.kind !== null && (
                    <Button
                      onClick={() => {
                        update({ kind: null });
                      }}
                    >
                      Show all events
                    </Button>
                  )}
                  {widerButton}
                </span>
              }
            >
              {view.kind === null &&
                "Blocked prompts and tool calls, values redacted from what LucentPad stores, and budget alerts show up here as they happen."}
            </EmptyState>
          ) : (
            <>
              <EventsTable events={events} fresh={fresh} stale={eventsQuery.isPlaceholderData} />
              {eventsQuery.isFetchNextPageError && (
                <p className={traceStyles.pageError} role="alert">
                  Couldn&apos;t load more events: {eventsQuery.error.message}
                </p>
              )}
              {eventsQuery.hasNextPage && !eventsQuery.isPlaceholderData && (
                <div className={traceStyles.more}>
                  <Button
                    onClick={() => {
                      void eventsQuery.fetchNextPage();
                    }}
                    disabled={eventsQuery.isFetchingNextPage}
                  >
                    {eventsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
                  </Button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
