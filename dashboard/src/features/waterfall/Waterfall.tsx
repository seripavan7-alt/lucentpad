import { useMemo } from "react";
import type { Span } from "../../api/types";
import { statusLabel } from "../../lib/labels";
import { formatDuration } from "../../lib/format";
import { eventLabel, eventTone } from "./events";
import { spanSubtitle } from "./spanInfo";
import { buildWaterfall, timeTicks, type WaterfallRow } from "./layout";
import styles from "./Waterfall.module.css";

interface WaterfallProps {
  /** Spans in any order; the list may grow over time (live traces). */
  spans: readonly Span[];
  selectedSpanId?: string | null;
  onSelect?: (spanId: string) => void;
  /** Extend the time axis to this epoch ms (for traces still in progress). */
  until?: number;
}

const INDENT_PX = 14;

export function Waterfall({ spans, selectedSpanId, onSelect, until }: WaterfallProps) {
  const layout = useMemo(() => buildWaterfall(spans, { until }), [spans, until]);
  const ticks = useMemo(() => timeTicks(layout.totalMs), [layout.totalMs]);

  if (layout.rows.length === 0) {
    return <p className={styles.empty}>No spans recorded for this trace.</p>;
  }

  return (
    <div className={styles.waterfall} role="list" aria-label="Spans">
      <div className={styles.header} aria-hidden="true">
        <div className={styles.headerName}>Span</div>
        <div className={styles.axis}>
          {ticks.map((tick) => (
            <span
              key={tick.ms}
              className={styles.tick}
              style={{ left: `${tick.pct}%` }}
              data-edge={tick.pct > 92 ? "end" : undefined}
            >
              {formatDuration(tick.ms)}
            </span>
          ))}
        </div>
      </div>
      <div className={styles.rows}>
        <div className={styles.gridlines} aria-hidden="true">
          <div className={styles.gridTrack}>
            {ticks.map((tick) => (
              <span key={tick.ms} className={styles.gridline} style={{ left: `${tick.pct}%` }} />
            ))}
          </div>
        </div>
        {layout.rows.map((row) => (
          <WaterfallRowView
            key={row.span.span_id}
            row={row}
            selected={row.span.span_id === selectedSpanId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}

function WaterfallRowView({
  row,
  selected,
  onSelect,
}: {
  row: WaterfallRow;
  selected: boolean;
  onSelect: ((spanId: string) => void) | undefined;
}) {
  const { span } = row;
  const subtitle = spanSubtitle(span);
  const barEnd = row.offsetPct + row.widthPct;
  const labelAfter = barEnd < 82;
  return (
    <div role="listitem">
      <button
        type="button"
        className={styles.row}
        aria-pressed={selected}
        data-kind={span.kind}
        data-status={span.status}
        data-depth={row.depth}
        onClick={() => onSelect?.(span.span_id)}
      >
        <span
          className={styles.nameCell}
          style={{ paddingLeft: `${row.depth * INDENT_PX + 12}px` }}
        >
          <span className={styles.kindSwatch} aria-hidden="true" />
          <span className={styles.name}>{span.name}</span>
          {subtitle && <span className={styles.subtitle}>{subtitle}</span>}
          {span.status !== "ok" && (
            <span className={styles.statusFlag} data-status={span.status}>
              {statusLabel(span.status)}
            </span>
          )}
        </span>
        <span className={styles.track}>
          <span
            className={styles.bar}
            data-testid="span-bar"
            style={{ left: `${row.offsetPct}%`, width: `${row.widthPct}%` }}
          />
          <span
            className={styles.duration}
            style={
              labelAfter
                ? { left: `calc(${barEnd}% + 6px)` }
                : { right: `calc(${100 - row.offsetPct}% + 6px)` }
            }
          >
            {formatDuration(row.durationMs)}
          </span>
          {row.events.map((marker, i) => {
            const label = `${eventLabel(marker.event.name)} at +${formatDuration(marker.offsetMs)}`;
            return (
              <span
                key={`${marker.event.name}-${i}`}
                className={styles.marker}
                data-tone={eventTone(marker.event.name)}
                style={{ left: `${marker.offsetPct}%` }}
                role="img"
                aria-label={label}
                title={label}
              />
            );
          })}
        </span>
      </button>
    </div>
  );
}
