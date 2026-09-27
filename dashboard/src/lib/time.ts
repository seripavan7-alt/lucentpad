/**
 * An ISO timestamp as epoch microseconds, keeping the sub-millisecond digits `Date.parse` drops
 * (the API sends microseconds), so turns order exactly like the server's `timestamptz` sort.
 * NaN for anything unparseable.
 */
export function isoMicros(iso: string): number {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return NaN;
  const frac = /\.(\d+)/.exec(iso)?.[1] ?? "";
  const extra = Number((frac.slice(3, 6) || "0").padEnd(3, "0"));
  return ms * 1000 + extra;
}
