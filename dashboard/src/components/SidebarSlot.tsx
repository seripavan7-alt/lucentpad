import { createContext, useContext } from "react";

/**
 * A spot in the app sidebar, below the main nav, where the current page can render its own
 * controls (the Traces page puts its filters there) with `createPortal`. Null until mounted.
 */
export const SidebarSlotContext = createContext<HTMLElement | null>(null);

export function useSidebarSlot(): HTMLElement | null {
  return useContext(SidebarSlotContext);
}
