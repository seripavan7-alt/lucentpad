import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { vi } from "vitest";
import { AppRoutes } from "../App";

type Handler = (url: URL) => unknown;

export interface FetchMock {
  fn: ReturnType<typeof vi.fn>;
  /** Parsed URLs of every request made, in order. */
  urls: () => URL[];
}

export class HttpError {
  constructor(
    readonly status: number,
    readonly body: unknown = { detail: "error" },
  ) {}
}

const AS_OF = "2026-09-25T10:00:00Z";

/** Valid empty answers for list/summary routes a test didn't mock (pages render empty). */
const EMPTY_BODIES: Record<string, unknown> = {
  "/v1/costs": {
    points: [],
    bucket_seconds: 1800,
    group_by: "model",
    total_cost_usd: 0,
    as_of: AS_OF,
  },
  "/v1/pricing": { prices: [], checked: "2026-09-25" },
  "/v1/guardrails/rules": { rules: [], source: "built-in", version: "0" },
  "/v1/guardrails/events": { events: [], next_cursor: null, as_of: AS_OF },
  "/v1/guardrails/summary": { blocks: [], redactions: [], budget_alerts: 0, as_of: AS_OF },
  "/v1/evals/runs": { runs: [], next_cursor: null },
};

/**
 * Stub global fetch with a router keyed by pathname. A handler returns a JSON body,
 * an HttpError for a non-2xx response, or throws to simulate a network failure.
 * Unmatched M3 list routes get a valid empty body; anything else (e.g. /healthz) gets a
 * 200 `{status: "ok"}`.
 */
export function mockFetch(routes: Record<string, Handler>): FetchMock {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    await Promise.resolve();
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, "http://localhost");
    const handler =
      routes[url.pathname] ??
      Object.entries(routes).find(([pattern]) => matchPattern(pattern, url.pathname))?.[1];
    const body = handler ? handler(url) : (EMPTY_BODIES[url.pathname] ?? { status: "ok" });
    if (body instanceof HttpError) {
      return new Response(JSON.stringify(body.body), {
        status: body.status,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return {
    fn,
    urls: () =>
      fn.mock.calls.map(([input]) => {
        const raw =
          typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        return new URL(raw, "http://localhost");
      }),
  };
}

function matchPattern(pattern: string, pathname: string): boolean {
  if (!pattern.includes(":")) return false;
  const re = new RegExp(`^${pattern.replace(/:[^/]+/g, "[^/]+")}$`);
  return re.test(pathname);
}

let currentLocation = "";

function LocationProbe() {
  const location = useLocation();
  useEffect(() => {
    currentLocation = location.pathname + location.search;
  }, [location]);
  return null;
}

/** The router location (pathname + search) as of the last render. */
export function getLocation(): string {
  return currentLocation;
}

export function renderApp(route = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AppRoutes />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
