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

/**
 * Stub global fetch with a router keyed by pathname. A handler returns a JSON body,
 * an HttpError for a non-2xx response, or throws to simulate a network failure.
 * Unmatched paths (e.g. /healthz) get a 200 `{status: "ok"}`.
 */
export function mockFetch(routes: Record<string, Handler>): FetchMock {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    await Promise.resolve();
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, "http://localhost");
    const handler =
      routes[url.pathname] ??
      Object.entries(routes).find(([pattern]) => matchPattern(pattern, url.pathname))?.[1];
    const body = handler ? handler(url) : { status: "ok" };
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
