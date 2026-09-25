import { useEffect, useRef } from "react";

/** Longest wait between polls after repeated errors. */
export const MAX_BACKOFF_MS = 30_000;

/** Delay before the next poll: the interval, doubled per consecutive failure, capped. */
export function nextPollDelay(
  intervalMs: number,
  failures: number,
  maxMs = MAX_BACKOFF_MS,
): number {
  if (failures <= 0) return intervalMs;
  return Math.min(intervalMs * 2 ** failures, Math.max(maxMs, intervalMs));
}

function isHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/**
 * Run `task` every `intervalMs` (the first run one interval after mount), one run at a time.
 *
 * - Hidden tabs don't poll: a tick that finds the tab hidden waits for `visibilitychange`
 *   and runs as soon as the tab is visible again.
 * - Errors back off: interval × 2^failures, capped at `MAX_BACKOFF_MS`; a success resets it.
 * - Unmount (or `enabled` → false, or a new interval) cancels the pending tick and aborts
 *   the running one through the signal.
 */
export function usePolling(
  task: (signal: AbortSignal) => Promise<void>,
  { intervalMs, enabled = true }: { intervalMs: number; enabled?: boolean },
): void {
  const taskRef = useRef(task);
  useEffect(() => {
    taskRef.current = task;
  });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let waitingForVisible = false;

    const schedule = (delay: number) => {
      timer = setTimeout(() => {
        void tick();
      }, delay);
    };

    const tick = async () => {
      timer = undefined;
      if (isHidden()) {
        waitingForVisible = true;
        return;
      }
      try {
        await taskRef.current(controller.signal);
        failures = 0;
      } catch {
        failures += 1;
      }
      if (controller.signal.aborted) return;
      schedule(nextPollDelay(intervalMs, failures));
    };

    const onVisibility = () => {
      if (waitingForVisible && !isHidden()) {
        waitingForVisible = false;
        void tick();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    schedule(intervalMs);
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, intervalMs]);
}
