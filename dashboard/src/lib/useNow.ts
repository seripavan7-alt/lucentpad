import { useEffect, useState } from "react";

/**
 * Current time, refreshed on an interval so relative timestamps stay fresh. With
 * `enabled` false the value is still correct as of the last render, just not ticking.
 */
export function useNow(intervalMs = 30_000, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      setNow(Date.now());
    }, intervalMs);
    return () => {
      clearInterval(id);
    };
  }, [intervalMs, enabled]);
  return now;
}
