import { useLayoutEffect, useRef, type RefObject } from "react";

function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let node = el?.parentElement ?? null; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
  }
  return null;
}

/**
 * Keep what the user is looking at in place when rows are prepended above it: if the list
 * is scrolled, shift the scroll position by the height the new rows added. Rows are found by
 * `[<attr>="<id>"]` inside `container` (ids are hex, no escaping needed); `firstId` is the id of the list's first row.
 */
export function useScrollAnchor(
  container: RefObject<HTMLElement | null>,
  attr: string,
  firstId: string,
) {
  const anchor = useRef<{ id: string; top: number } | null>(null);
  useLayoutEffect(() => {
    const body = container.current;
    if (!body) return;
    const find = (id: string) => body.querySelector<HTMLElement>(`[${attr}="${id}"]`);
    const prev = anchor.current;
    if (prev && prev.id !== firstId) {
      const row = find(prev.id);
      const scroller = scrollParent(body);
      if (row && scroller && scroller.scrollTop > 0) {
        scroller.scrollTop += row.offsetTop - prev.top;
      }
    }
    const first = firstId ? find(firstId) : null;
    anchor.current = first ? { id: firstId, top: first.offsetTop } : null;
  }, [container, attr, firstId]);
}
