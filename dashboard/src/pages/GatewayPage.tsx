import { useMemo } from "react";
import { useGatewaySummary, useGatewayTurns, useLiveGatewayTurns } from "../api/queries";
import { GATEWAY_CLIENTS, type GatewayClient } from "../api/types";
import { RefreshIcon } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl, type SegmentOption } from "../components/SegmentedControl";
import { Button, EmptyState, ErrorState } from "../components/States";
import { ClientTotals } from "../features/gateway/ClientTotals";
import { GatewaySetup } from "../features/gateway/GatewaySetup";
import { groupSessions } from "../features/gateway/sessions";
import { TurnFeed } from "../features/gateway/TurnFeed";
import { useGatewayView } from "../features/gateway/view";
import { RANGES, rangeInfo, rangePhrase, type RangeId } from "../features/traces/view";
import { clientLabel } from "../lib/format";
import styles from "./GatewayPage.module.css";
import traceStyles from "./TracesPage.module.css";

const RANGE_OPTIONS: SegmentOption<RangeId>[] = RANGES.map((r) => ({ value: r.id, label: r.id }));

type ClientOption = GatewayClient | "all";
const CLIENT_OPTIONS: SegmentOption<ClientOption>[] = [
  { value: "all", label: "All" },
  ...GATEWAY_CLIENTS.map((c) => ({ value: c, label: clientLabel(c) ?? c })),
];

/** The next wider preset an empty view offers: 24 hours, or 30 days from 24h and up. */
function widerRange(range: RangeId): RangeId | null {
  const ms = rangeInfo(range).ms;
  if (ms < rangeInfo("24h").ms) return "24h";
  if (ms < rangeInfo("30d").ms) return "30d";
  return null;
}

export function GatewayPage() {
  const [view, update] = useGatewayView();
  const turnsQuery = useGatewayTurns(view);
  const summary = useGatewaySummary(view.range);
  const fresh = useLiveGatewayTurns(view, turnsQuery.isSuccess);

  const turns = useMemo(
    () => turnsQuery.data?.pages.flatMap((page) => page.turns) ?? [],
    [turnsQuery.data],
  );
  const sessions = useMemo(() => groupSessions(turns), [turns]);
  const wider = widerRange(view.range);
  // No gateway traffic at all in the range (not just none for the chosen client): show setup.
  const noTraffic =
    turns.length === 0 &&
    !turnsQuery.isPlaceholderData &&
    (view.client === null || summary.data?.clients.length === 0);

  const refresh = () => {
    void turnsQuery.refetch();
    void summary.refetch();
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

  return (
    <>
      <PageHeader
        title="Gateway"
        actions={
          <>
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
              className={traceStyles.iconButton}
              aria-label="Refresh"
              title="Refresh"
              onClick={refresh}
              data-spinning={
                turnsQuery.isRefetching && !turnsQuery.isFetchingNextPage ? "true" : undefined
              }
            >
              <RefreshIcon width={14} height={14} />
            </button>
          </>
        }
      />
      <div className={styles.body}>
        {turnsQuery.isPending ? (
          <FeedSkeleton />
        ) : turnsQuery.data === undefined ? (
          <ErrorState
            title="Couldn't load gateway turns"
            action={
              <Button
                onClick={() => {
                  void turnsQuery.refetch();
                }}
              >
                Retry
              </Button>
            }
          >
            {turnsQuery.error.message}
          </ErrorState>
        ) : noTraffic ? (
          <GatewaySetup
            title={`No gateway traffic in ${rangePhrase(view.range)}`}
            action={widerButton}
          />
        ) : (
          <>
            <ClientTotals
              summary={summary.data}
              selected={view.client}
              loading={summary.isPending}
            />
            <div className={styles.toolbar}>
              <h2 className={styles.feedTitle}>Turns</h2>
              <SegmentedControl
                label="Client"
                options={CLIENT_OPTIONS}
                value={view.client ?? "all"}
                onChange={(value) => {
                  update({ client: value === "all" ? null : value });
                }}
              />
            </div>
            {turns.length === 0 ? (
              <EmptyState
                title={`No ${
                  view.client === "other"
                    ? "turns from other clients"
                    : `${clientLabel(view.client) ?? ""} turns`
                } in ${rangePhrase(view.range)}`}
                action={
                  <span className={traceStyles.emptyActions}>
                    <Button
                      onClick={() => {
                        update({ client: null });
                      }}
                    >
                      Show all clients
                    </Button>
                    {widerButton}
                  </span>
                }
              />
            ) : (
              <>
                <TurnFeed sessions={sessions} fresh={fresh} stale={turnsQuery.isPlaceholderData} />
                {turnsQuery.isFetchNextPageError && (
                  <p className={traceStyles.pageError} role="alert">
                    Couldn&apos;t load more turns: {turnsQuery.error.message}
                  </p>
                )}
                {turnsQuery.hasNextPage && !turnsQuery.isPlaceholderData && (
                  <div className={traceStyles.more}>
                    <Button
                      onClick={() => {
                        void turnsQuery.fetchNextPage();
                      }}
                      disabled={turnsQuery.isFetchingNextPage}
                    >
                      {turnsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
                    </Button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}

function FeedSkeleton() {
  return (
    <div className={traceStyles.skeleton} aria-busy="true" aria-label="Loading gateway turns">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className={traceStyles.skeletonRow} />
      ))}
    </div>
  );
}
