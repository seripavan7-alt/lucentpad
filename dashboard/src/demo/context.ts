import { createContext, useContext } from "react";

/** Set only in the static demo build: where "back to the site" links go. */
export interface DemoMode {
  siteHref: string;
}

export const DemoModeContext = createContext<DemoMode | null>(null);

export function useDemoMode(): DemoMode | null {
  return useContext(DemoModeContext);
}
