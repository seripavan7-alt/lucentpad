import type { CSSProperties, ReactNode } from "react";
import styles from "./StatTiles.module.css";

export interface StatTile {
  label: string;
  value: ReactNode;
  meta?: ReactNode;
  /** Quiet the value (nothing counted yet). */
  empty?: boolean;
}

/** A row of quiet number tiles (same look as the Gateway page's client totals). */
export function StatTiles({
  label,
  tiles,
  loading = false,
  columns,
}: {
  label: string;
  tiles: StatTile[];
  loading?: boolean;
  /** Grid columns (default: one per tile, at most four). */
  columns?: number;
}) {
  return (
    <section
      className={styles.tiles}
      aria-label={label}
      aria-busy={loading}
      style={{ "--tile-count": columns ?? tiles.length } as CSSProperties}
    >
      {tiles.map((t) => (
        <div
          key={t.label}
          className={styles.tile}
          role="group"
          aria-label={t.label}
          data-empty={t.empty ? "true" : undefined}
        >
          <span className={styles.label}>{t.label}</span>
          <span className={styles.value}>{loading ? "–" : t.value}</span>
          {t.meta !== undefined && <span className={`${styles.meta} num`}>{t.meta}</span>}
        </div>
      ))}
    </section>
  );
}
