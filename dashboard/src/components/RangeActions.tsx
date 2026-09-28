import { RANGE_OPTIONS, type RangeId } from "../features/traces/view";
import traceStyles from "../pages/TracesPage.module.css";
import { RefreshIcon } from "./icons";
import { SegmentedControl } from "./SegmentedControl";

/** The page header's time-range presets and refresh button (as on Traces and Gateway). */
export function RangeActions({
  range,
  onRange,
  onRefresh,
  refreshing,
}: {
  range: RangeId;
  onRange: (range: RangeId) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  return (
    <>
      <SegmentedControl
        label="Time range"
        options={RANGE_OPTIONS}
        value={range}
        onChange={onRange}
      />
      <button
        type="button"
        className={traceStyles.iconButton}
        aria-label="Refresh"
        title="Refresh"
        onClick={onRefresh}
        data-spinning={refreshing ? "true" : undefined}
      >
        <RefreshIcon width={14} height={14} />
      </button>
    </>
  );
}

/** Loading rows, same as the Traces list's. */
export function ListSkeleton({ label, rows = 8 }: { label: string; rows?: number }) {
  return (
    <div className={traceStyles.skeleton} aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className={traceStyles.skeletonRow} />
      ))}
    </div>
  );
}
