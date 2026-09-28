import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { formatCost } from "../../lib/format";
import { useElementWidth } from "../../lib/useElementWidth";
import styles from "./CostChart.module.css";
import {
  formatAxisCost,
  formatBucket,
  formatBucketRange,
  labelEvery,
  niceScale,
  seriesColor,
  type CostChartData,
} from "./series";

const PLOT_HEIGHT = 200;
const AXIS_BAND = 24;
const TOP_PAD = 18; // room for the peak's direct label
const Y_LABELS = 52;
const BAR_MAX = 24;
const GAP = 2; // surface gap between stacked segments
const RADIUS = 4;

/** A column's top segment: rounded data end, square at the bottom. */
function roundedTop(x: number, y: number, w: number, h: number): string {
  const r = Math.min(RADIUS, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

interface Props {
  data: CostChartData;
  /** A series to bring forward (legend hover); the rest recede. */
  focusKey?: string | null;
}

/**
 * Stacked columns of spend per time bucket. Hovering (or arrowing through, once focused) a
 * column shows every group's spend in that bucket; the peak column carries its total.
 */
export function CostChart({ data, focusKey = null }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const width = useElementWidth(wrap, 720);
  const [active, setActive] = useState<number | null>(null);

  const { columns, groups, bucketMs } = data;
  const byKey = useMemo(() => new Map(groups.map((g) => [g.key, g])), [groups]);
  const peak = columns.reduce(
    (best, c, i) => (c.total > (columns[best]?.total ?? 0) ? i : best),
    0,
  );
  const scale = niceScale(columns[peak]?.total ?? 0);
  const plotW = Math.max(40, width - Y_LABELS);
  const slot = plotW / Math.max(1, columns.length);
  const barW = Math.max(1, Math.min(BAR_MAX, slot * 0.64));
  const y = (usd: number) => TOP_PAD + PLOT_HEIGHT * (1 - usd / scale.max);
  const every = labelEvery(columns.length, Math.max(2, Math.floor(plotW / 90)));
  const ticks = Array.from(
    { length: Math.round(scale.max / scale.step) + 1 },
    (_, i) => i * scale.step,
  );
  const height = TOP_PAD + PLOT_HEIGHT + AXIS_BAND;

  const onKeyDown = (e: KeyboardEvent) => {
    if (!columns.length) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const d = e.key === "ArrowRight" ? 1 : -1;
      setActive((i) => Math.min(columns.length - 1, Math.max(0, (i ?? columns.length - 1) + d)));
    } else if (e.key === "Escape") {
      setActive(null);
    }
  };

  const current = active === null ? undefined : columns[active];
  const tipX = active === null ? 0 : Y_LABELS + slot * (active + 0.5);
  const flip = tipX > width * 0.6;

  return (
    <div
      ref={wrap}
      className={styles.chart}
      tabIndex={0}
      role="group"
      aria-label="Spend over time. Use the arrow keys to read each bucket."
      onKeyDown={onKeyDown}
      onFocus={() => {
        setActive((i) => i ?? Math.max(0, columns.length - 1));
      }}
      onBlur={() => {
        setActive(null);
      }}
      onPointerLeave={() => {
        setActive(null);
      }}
    >
      <svg width={width} height={height} role="img" aria-label="Stacked columns of spend">
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={Y_LABELS}
              x2={width}
              y1={y(t)}
              y2={y(t)}
              className={t === 0 ? styles.baseline : styles.grid}
            />
            <text x={Y_LABELS - 8} y={y(t)} dy="0.32em" textAnchor="end" className={styles.tick}>
              {formatAxisCost(t, scale.step)}
            </text>
          </g>
        ))}
        {active !== null && (
          <rect
            x={Y_LABELS + slot * active}
            y={TOP_PAD}
            width={slot}
            height={PLOT_HEIGHT}
            className={styles.band}
          />
        )}
        {columns.map((col, i) => {
          const x = Y_LABELS + slot * i + (slot - barW) / 2;
          let base = y(0);
          const last = col.segments.length - 1;
          return (
            <g key={col.start} data-bucket={col.start}>
              {col.segments.map((seg, j) => {
                const h = PLOT_HEIGHT * (seg.cost / scale.max);
                const top = base - h;
                const gap = j > 0 ? GAP : 0;
                const drawn = h - gap;
                base = top;
                if (drawn < 0.5) return null;
                const group = byKey.get(seg.key);
                const fill = seriesColor(group?.slot ?? 0);
                const dim = focusKey !== null && focusKey !== seg.key;
                return j === last ? (
                  <path
                    key={seg.key}
                    d={roundedTop(x, top, barW, drawn)}
                    fill={fill}
                    opacity={dim ? 0.25 : 1}
                  />
                ) : (
                  <rect
                    key={seg.key}
                    x={x}
                    y={top}
                    width={barW}
                    height={drawn}
                    fill={fill}
                    opacity={dim ? 0.25 : 1}
                  />
                );
              })}
            </g>
          );
        })}
        {columns[peak] && columns[peak].total > 0 && (
          <text
            x={Y_LABELS + slot * (peak + 0.5)}
            y={y(columns[peak].total) - 6}
            textAnchor="middle"
            className={styles.peak}
          >
            {formatCost(columns[peak].total)}
          </text>
        )}
        {columns.map((col, i) =>
          i % every === 0 ? (
            <text
              key={col.start}
              x={Y_LABELS + slot * (i + 0.5)}
              y={TOP_PAD + PLOT_HEIGHT + 16}
              textAnchor="middle"
              className={styles.tick}
            >
              {formatBucket(col.start, bucketMs)}
            </text>
          ) : null,
        )}
        {/* Hit targets: the whole column slot, taller and wider than the marks. */}
        {columns.map((col, i) => (
          <rect
            key={col.start}
            x={Y_LABELS + slot * i}
            y={0}
            width={slot}
            height={TOP_PAD + PLOT_HEIGHT}
            fill="transparent"
            data-testid="cost-column"
            onPointerEnter={() => {
              setActive(i);
            }}
          />
        ))}
      </svg>
      {current && (
        <div
          className={styles.tooltip}
          role="status"
          style={{
            left: tipX,
            transform: flip ? "translateX(calc(-100% - 12px))" : "translateX(12px)",
          }}
        >
          <p className={styles.tipTitle}>{formatBucketRange(current.start, bucketMs)}</p>
          {current.segments.length === 0 ? (
            <p className={styles.tipEmpty}>No spend</p>
          ) : (
            <ul className={styles.tipRows}>
              {[...current.segments].reverse().map((seg) => {
                const group = byKey.get(seg.key);
                return (
                  <li key={seg.key} className={styles.tipRow}>
                    <span
                      className={styles.tipKey}
                      style={{ background: seriesColor(group?.slot ?? 0) }}
                      aria-hidden="true"
                    />
                    <span className={`${styles.tipValue} num`}>{formatCost(seg.cost)}</span>
                    <span className={styles.tipLabel}>{group?.label ?? seg.key}</span>
                  </li>
                );
              })}
              {current.segments.length > 1 && (
                <li className={`${styles.tipRow} ${styles.tipTotal}`}>
                  <span className={styles.tipKey} aria-hidden="true" />
                  <span className={`${styles.tipValue} num`}>{formatCost(current.total)}</span>
                  <span className={styles.tipLabel}>Total</span>
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
