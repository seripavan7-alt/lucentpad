import { useSyncExternalStore } from "react";

/** The viewer's "Hide sample data" choice, kept in localStorage and shared across the app. */
export const HIDE_SAMPLE_KEY = "lucentpad.hideSample";

const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return window.localStorage.getItem(HIDE_SAMPLE_KEY) === "1";
  } catch {
    return false;
  }
}

let current = read();

export function setHideSamplePreference(hide: boolean): void {
  current = hide;
  try {
    if (hide) window.localStorage.setItem(HIDE_SAMPLE_KEY, "1");
    else window.localStorage.removeItem(HIDE_SAMPLE_KEY);
  } catch {
    // Storage unavailable; the choice lasts for this session only.
  }
  for (const listener of listeners) listener();
}

export function useHideSamplePreference(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      listeners.add(onChange);
      return () => {
        listeners.delete(onChange);
      };
    },
    () => current,
    () => false,
  );
}
