import { useId, useState } from "react";
import { FACETS, type Facet, type FacetValue, type TraceFacets } from "../../api/types";
import type { ComponentType, SVGProps } from "react";
import {
  ChevronDownIcon,
  ClientIcon,
  CloseIcon,
  ModelIcon,
  ServiceIcon,
  SourceIcon,
  StatusIcon,
  TagIcon,
} from "../../components/icons";
import { formatInteger } from "../../lib/format";
import {
  activeFilterCount,
  FACET_LABELS,
  facetRows,
  facetValueLabel,
  NO_FILTERS,
  type FacetSelection,
} from "./view";
import styles from "./FilterPanel.module.css";

/** Facets with more values than this get a search box (Name always has one). */
const SEARCH_THRESHOLD = 10;
/** Values shown before "Show all" (selected values always show). */
const PREVIEW_VALUES = 5;
const FACET_ICONS: Record<Facet, ComponentType<SVGProps<SVGSVGElement>>> = {
  name: TagIcon,
  status: StatusIcon,
  source: SourceIcon,
  client: ClientIcon,
  model: ModelIcon,
  service: ServiceIcon,
};

interface FilterPanelProps {
  facets: TraceFacets | undefined;
  loading: boolean;
  error: boolean;
  filters: FacetSelection;
  onChange: (filters: Partial<FacetSelection>) => void;
  /** Narrow screens: the panel is shown only when opened with the "Filters" button. */
  open: boolean;
  id?: string;
}

/**
 * Left-hand filter panel of the Traces page: one collapsible group per facet with value counts
 * (from `GET /v1/traces/facets`, each facet excluding its own filter), sorted by count.
 */
export function FilterPanel({
  facets,
  loading,
  error,
  filters,
  onChange,
  open,
  id,
}: FilterPanelProps) {
  const active = activeFilterCount(filters);
  return (
    <aside className={styles.panel} aria-label="Filters" data-open={open} id={id}>
      <div className={styles.head}>
        <h2 className={styles.heading}>Filters</h2>
        {active > 0 && (
          <button
            type="button"
            className={styles.textButton}
            onClick={() => {
              onChange(NO_FILTERS);
            }}
          >
            Clear all ({active})
          </button>
        )}
      </div>
      {error && <p className={styles.note}>Couldn&apos;t load filter counts.</p>}
      {FACETS.map((facet) => (
        <FacetGroup
          key={facet}
          facet={facet}
          values={facets?.[facet]}
          loading={loading && facets === undefined}
          selected={filters[facet]}
          onChange={(values) => {
            onChange({ [facet]: values });
          }}
        />
      ))}
    </aside>
  );
}

function FacetGroup({
  facet,
  values,
  loading,
  selected,
  onChange,
}: {
  facet: Facet;
  values: readonly FacetValue[] | undefined;
  loading: boolean;
  selected: readonly string[];
  onChange: (values: string[]) => void;
}) {
  const [expanded, setExpanded] = useState(
    // Every group starts closed, unless something in it is already selected (e.g. from the URL).
    () => selected.length > 0,
  );
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState("");
  const bodyId = useId();
  const label = FACET_LABELS[facet];
  const FacetIcon = FACET_ICONS[facet];
  const rows = facetRows(values, selected);
  const searchable = facet === "name" || rows.length > SEARCH_THRESHOLD;
  const needle = search.trim().toLowerCase();
  const visible =
    searchable && needle
      ? rows.filter(
          (r) =>
            selected.includes(r.value) ||
            r.value.toLowerCase().includes(needle) ||
            facetValueLabel(facet, r.value).toLowerCase().includes(needle),
        )
      : rows;
  const hidden = needle || showAll ? 0 : Math.max(0, visible.length - PREVIEW_VALUES);
  const shown =
    hidden > 0
      ? visible.filter((r, i) => i < PREVIEW_VALUES || selected.includes(r.value))
      : visible;

  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  };

  return (
    <section className={styles.group} aria-label={label}>
      <div className={styles.groupHead}>
        <button
          type="button"
          className={styles.groupToggle}
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => {
            setExpanded((v) => !v);
          }}
        >
          <FacetIcon className={styles.groupIcon} />
          <span className={styles.groupLabel}>{label}</span>
          <ChevronDownIcon width={12} height={12} className={styles.chevron} />
        </button>
        {selected.length > 0 && (
          <button
            type="button"
            className={styles.clearChip}
            aria-label={`Clear ${label}`}
            title={`Clear ${label}`}
            onClick={() => {
              onChange([]);
            }}
          >
            <span className="num">{selected.length}</span>
            <CloseIcon width={10} height={10} />
          </button>
        )}
      </div>
      {expanded && (
        <div id={bodyId} className={styles.groupBody}>
          {searchable && (
            <input
              type="search"
              className={styles.search}
              placeholder={`Search ${label.toLowerCase()}`}
              aria-label={`Search ${label}`}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
              }}
            />
          )}
          {loading ? (
            <p className={styles.note}>Loading…</p>
          ) : visible.length === 0 ? (
            <p className={styles.note}>{needle ? "No matches" : "None in this range"}</p>
          ) : (
            <ul className={styles.values}>
              {shown.map((row) => {
                const text = facetValueLabel(facet, row.value);
                const checked = selected.includes(row.value);
                return (
                  <li key={row.value}>
                    <label className={styles.value} data-checked={checked} title={row.value}>
                      <input
                        type="checkbox"
                        className={styles.checkbox}
                        aria-label={text}
                        checked={checked}
                        onChange={() => {
                          toggle(row.value);
                        }}
                      />
                      <span className={styles.box} aria-hidden="true">
                        <svg viewBox="0 0 12 12" width="10" height="10">
                          <path d="M2.6 6.3 5 8.6l4.4-5" pathLength={1} />
                        </svg>
                      </span>
                      <span
                        className={
                          facet === "model" ? `${styles.valueText} mono` : styles.valueText
                        }
                      >
                        {text}
                      </span>
                      <span className={`${styles.count} num`} data-testid="facet-count">
                        {formatInteger(row.count)}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          {hidden > 0 && (
            <button
              type="button"
              className={styles.more}
              onClick={() => {
                setShowAll(true);
              }}
            >
              Show {hidden} more
            </button>
          )}
        </div>
      )}
    </section>
  );
}
