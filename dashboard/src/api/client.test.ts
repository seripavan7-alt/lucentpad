import { describe, expect, it, vi } from "vitest";
import { HttpError, mockFetch } from "../test/render";
import { traceListPage1 } from "../test/fixtures";
import { ApiError, getTrace, getTraceFacets, listTraces } from "./client";

describe("api client", () => {
  it("builds the list query with only the params that are set", async () => {
    const mock = mockFetch({ "/v1/traces": () => traceListPage1 });
    const res = await listTraces({
      limit: 50,
      cursor: null,
      source: ["gateway"],
      status: undefined,
    });
    expect(res.traces).toHaveLength(3);
    const url = mock.urls()[0]!;
    expect(url.pathname).toBe("/v1/traces");
    expect([...url.searchParams]).toEqual([
      ["limit", "50"],
      ["source", "gateway"],
    ]);
  });

  it("raises ApiError with the server's detail", async () => {
    mockFetch({ "/v1/traces/:id": () => new HttpError(404, { detail: "Trace not found" }) });
    await expect(getTrace("abc")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      message: "Trace not found",
    });
  });

  it("maps network failures to ApiError status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const err: unknown = await listTraces().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
  });

  it("sends facet filters as repeated params and since on trace detail", async () => {
    const mock = mockFetch({
      "/v1/traces/facets": () => ({}),
      "/v1/traces/:id": () => ({}),
    });
    await getTraceFacets({ from: "2026-09-25T10:00:00Z", model: ["a", "b"] });
    await getTrace("abc", { since: "2026-09-25T10:00:01Z" });
    const [facets, detail] = mock.urls();
    expect(facets!.pathname).toBe("/v1/traces/facets");
    expect([...facets!.searchParams]).toEqual([
      ["from", "2026-09-25T10:00:00Z"],
      ["model", "a"],
      ["model", "b"],
    ]);
    expect(detail!.pathname).toBe("/v1/traces/abc");
    expect(detail!.searchParams.get("since")).toBe("2026-09-25T10:00:01Z");
  });
});
