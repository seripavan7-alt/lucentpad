import { useState } from "react";
import type { CostGroup, CostSeries } from "../../api/types";
import { SegmentedControl, type SegmentOption } from "../../components/SegmentedControl";
import { formatCost, formatInteger } from "../../lib/format";
import { CostChart } from "./CostChart";
import styles from "./CostBreakdown.module.css";
import { bucketPhrase, formatBucketRange, seriesColor, type CostChartData } from "./series";
import { GROUP_LABELS } from "./view";

const GROUP_OPTIONS: SegmentOption<CostGroup>[] = (["model", "client", "service"] as const).map(
  (g) => ({ value: g, label: GROUP_LABELS[g] }),
);

interface Props {
  data: CostChartData;
  series: CostSeries;
  group: CostGroup;
  onGroup: (group: CostGroup) => void;
  /** Previous view's numbers, shown while a new range or grouping loads. */
  stale?: boolean;
}

const share = (part: number, whole: number) =>
  whole > 0 ? `${Math.round((part / whole) * 100)}%` : "–";

/** "Spend over time": the group switch, a legend with per-group totals, and the chart or table. */
export function CostBreakdown({ data, series, group, onGroup, stale }: Props) {
  const [asTable, setAsTable] = useState(false);
  const [focusKey, setFocusKey] = useState<string | null>(null);

  return (
    <section
      className={styles.section}
      aria-labelledby="cost-breakdown-title"
      data-stale={stale ? "true" : undefined}
    >
      <div className={styles.toolbar}>
        <div className={styles.heading}>
          <h2 id="cost-breakdown-title" className={styles.title}>
            Spend over time
          </h2>
          <span className={styles.caption}>
            {bucketPhrase(series.bucket_seconds * 1000)} buckets
          </span>
        </div>
        <div className={styles.controls}>
          <SegmentedControl
            label="Group by"
            options={GROUP_OPTIONS}
            value={group}
            onChange={onGroup}
          />
          <button
            type="button"
            className={styles.toggle}
            aria-pressed={asTable}
            onClick={() => {
              setAsTable((v) => !v);
            }}
          >
            Table
          </button>
        </div>
      </div>

      <ul className={styles.legend} aria-label={`Spend by ${GROUP_LABELS[group].toLowerCase()}`}>
        {data.groups.map((g) => (
          <li
            key={g.key}
            className={styles.legendItem}
            onPointerEnter={() => {
              setFocusKey(g.key);
            }}
            onPointerLeave={() => {
              setFocusKey(null);
            }}
            title={`${formatInteger(g.calls)} calls`}
          >
            <span
              className={styles.swatch}
              style={{ background: seriesColor(g.slot) }}
              aria-hidden="true"
            />
            <span className={styles.legendLabel}>{g.label}</span>
            <span className={`${styles.legendCost} num`}>{formatCost(g.cost)}</span>
            <span className={`${styles.legendShare} num`}>{share(g.cost, data.total)}</span>
          </li>
        ))}
      </ul>

      {asTable ? (
        <BucketTable data={data} group={group} />
      ) : (
        <CostChart data={data} focusKey={focusKey} />
      )}
    </section>
  );
}

/** The chart as a table: one row per bucket with spend, newest first. */
function BucketTable({ data, group }: { data: CostChartData; group: CostGroup }) {
  const rows = data.columns.filter((c) => c.total > 0).reverse();
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="visually-hidden">
          Spend per {bucketPhrase(data.bucketMs)} bucket by {GROUP_LABELS[group].toLowerCase()}
        </caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            {data.groups.map((g) => (
              <th key={g.key} scope="col" className={styles.right}>
                {g.label}
              </th>
            ))}
            <th scope="col" className={styles.right}>
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((col) => {
            const cells = new Map(col.segments.map((s) => [s.key, s.cost]));
            return (
              <tr key={col.start}>
                <td className="num">{formatBucketRange(col.start, data.bucketMs)}</td>
                {data.groups.map((g) => {
                  const v = cells.get(g.key);
                  return (
                    <td key={g.key} className={`${styles.right} num`}>
                      {v === undefined ? <span className="muted">–</span> : formatCost(v)}
                    </td>
                  );
                })}
                <td className={`${styles.right} num`}>{formatCost(col.total)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
