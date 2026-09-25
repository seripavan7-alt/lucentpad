import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "prism.theme";
const PREFERENCES: readonly ThemePreference[] = ["light", "dark", "system"];

function isPreference(value: unknown): value is ThemePreference {
  return typeof value === "string" && (PREFERENCES as readonly string[]).includes(value);
}

export function readThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isPreference(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

function writeThemePreference(pref: ThemePreference): void {
  try {
    if (pref === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, pref);
  } catch {
    // Storage unavailable (private mode, blocked); the choice lasts for this session only.
  }
}

/** Explicit choices set data-theme; "system" removes it so prefers-color-scheme applies. */
export function applyThemePreference(pref: ThemePreference, root = document.documentElement): void {
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
}

export function useThemePreference(): [ThemePreference, (pref: ThemePreference) => void] {
  const [pref, setPref] = useState<ThemePreference>(readThemePreference);

  useEffect(() => {
    applyThemePreference(pref);
  }, [pref]);

  const update = useCallback((next: ThemePreference) => {
    writeThemePreference(next);
    setPref(next);
  }, []);

  return [pref, update];
}
