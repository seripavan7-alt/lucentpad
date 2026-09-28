import { useMemo } from "react";
import { ApiError } from "../api/client";
import {
  BUDGET_ALERTS_LIMIT,
  useBudgetAlerts,
  useCosts,
  usePricing,
  useTopCostTraces,
} from "../api/queries";
import table from "../components/DataTable.module.css";
import { PageHeader } from "../components/PageHeader";
import { ListSkeleton, RangeActions } from "../components/RangeActions";
import { StatTiles } from "../components/StatTiles";
import { Button, EmptyState, ErrorState } from "../components/States";
import { BudgetAlerts } from "../features/costs/BudgetAlerts";
import { CostBreakdown } from "../features/costs/CostBreakdown";
import { PriceList } from "../features/costs/PriceList";
import { buildCostChart } from "../features/costs/series";
import { TopTraces } from "../features/costs/TopTraces";
import { useCostsView } from "../features/costs/view";
import { rangeFrom, rangeInfo, rangePhrase, widerRange } from "../features/traces/view";
import { formatCost, formatInteger, formatTokens } from "../lib/format";
import styles from "./CostsPage.module.css";

const plural = (n: number, one: string, many: string) =>
  `${formatInteger(n)} ${n === 1 ? one : many}`;

const GROUP_NOUNS = {
  model: ["model", "models"],
  client: ["client", "clients"],
  service: ["service", "services"],
} as const;

export function CostsPage() {
  const [view, update] = useCostsView();
  const costs = useCosts(view);
  const top = useTopCostTraces(view.range);
  const budget = useBudgetAlerts(view.range);
  const pricing = usePricing();
  const wider = widerRange(view.range);

  const series = costs.data;
  const chart = useMemo(() => {
    if (!series) return null;
    const to = Date.parse(series.as_of);
    return buildCostChart(series, Date.parse(rangeFrom(view.range, to)), to);
  }, [series, view.range]);

  const refresh = () => {
    void costs.refetch();
    void top.refetch();
    void budget.refetch();
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

  const notImplemented = (error: Error | null) => error instanceof ApiError && error.status === 501;
  const alerts = budget.data?.events ?? [];
  // Quiet: nothing at all from a server without guardrail events.
  const budgetSection = !notImplemented(budget.error) && (
    <section aria-labelledby="budget-alerts-title">
      <div className={table.sectionHead}>
        <h2 id="budget-alerts-title" className={table.sectionTitle}>
          Budget alerts
          {budget.data?.next_cursor && (
            <span className={table.sectionNote}>latest {BUDGET_ALERTS_LIMIT}</span>
          )}
        </h2>
      </div>
      {budget.isPending ? (
        <ListSkeleton label="Loading budget alerts" rows={2} />
      ) : budget.isError && !budget.data ? (
        <p className={table.note} role="alert">
          Couldn&apos;t load budget alerts: {budget.error.message}
        </p>
      ) : alerts.length === 0 ? (
        <p className={table.note}>No budget alerts in {rangePhrase(view.range)}.</p>
      ) : (
        <BudgetAlerts alerts={alerts} stale={budget.isPlaceholderData} />
      )}
    </section>
  );

  let body;
  if (costs.isPending) {
    body = <ListSkeleton label="Loading costs" />;
  } else if (!series || !chart) {
    const notReady = costs.error instanceof ApiError && costs.error.status === 501;
    body = notReady ? (
      <EmptyState title="Cost queries aren't available yet">
        This LucentPad server doesn&apos;t answer cost queries. Update it to see spend over time.
      </EmptyState>
    ) : (
      <ErrorState
        title="Couldn't load costs"
        action={
          <Button
            onClick={() => {
              void costs.refetch();
            }}
          >
            Retry
          </Button>
        }
      >
        {costs.error?.message}
      </ErrorState>
    );
  } else if (chart.groups.length === 0) {
    body = (
      <EmptyState title={`No spend in ${rangePhrase(view.range)}`} action={widerButton}>
        Costs come from the model calls in your traces, priced with the list prices below.
      </EmptyState>
    );
  } else {
    const [one, many] = GROUP_NOUNS[view.group];
    const topTraces = top.data?.traces ?? [];
    body = (
      <>
        <StatTiles
          label="Totals for the range"
          tiles={[
            {
              label: "Spend",
              value: formatCost(chart.total),
              meta: chart.calls > 0 ? `${formatCost(chart.total / chart.calls)} per call` : "–",
            },
            {
              label: "LLM calls",
              value: formatInteger(chart.calls),
              meta: `across ${plural(chart.groups.length, one, many)}`,
            },
            {
              label: "Tokens",
              value: formatTokens(chart.inputTokens + chart.outputTokens),
              meta: `${formatTokens(chart.inputTokens)} in · ${formatTokens(chart.outputTokens)} out`,
            },
          ]}
        />
        <CostBreakdown
          data={chart}
          series={series}
          group={view.group}
          onGroup={(group) => {
            update({ group });
          }}
          stale={costs.isPlaceholderData}
        />
        <div className={table.sectionHead}>
          <h2 className={table.sectionTitle}>
            Most expensive traces
            <span className={table.sectionNote}>top {topTraces.length || 10} by cost</span>
          </h2>
        </div>
        {top.isPending ? (
          <ListSkeleton label="Loading traces" rows={4} />
        ) : top.isError && !top.data ? (
          <p className={table.note} role="alert">
            Couldn&apos;t load traces: {top.error.message}
          </p>
        ) : (
          <TopTraces traces={topTraces} stale={top.isPlaceholderData} />
        )}
        {budgetSection}
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Costs"
        actions={
          <RangeActions
            range={view.range}
            onRange={(range) => {
              update({ range });
            }}
            onRefresh={refresh}
            refreshing={costs.isRefetching}
          />
        }
      />
      <div className={styles.body}>
        {body}
        {pricing.data && <PriceList table={pricing.data} />}
      </div>
    </>
  );
}
