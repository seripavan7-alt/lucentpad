import { FACETS, type TraceDetail, type TraceFacets, type TraceList } from "../api/types";
import { createDemoFetch, freshShift, shiftSnapshot, type DemoSnapshot } from "./adapter";
import parityJson from "./parity.json";
import snapshotJson from "./snapshot.json";

const snapshot = snapshotJson as unknown as DemoSnapshot;

type Params = Record<string, string | number | readonly (string | number)[]>;

interface ParityQuery {
  params: Params;
  pages: { trace_ids: string[]; has_next: boolean }[];
}
/** Facet parity, if the snapshot test records it: `{params, facets}` per query. */
interface ParityFacets {
  params: Params;
  facets: TraceFacets;
}
const parity = parityJson as unknown as { queries: ParityQuery[]; facets?: ParityFacets[] };

const demoFetch = createDemoFetch(snapshot);

function url(path: string, params: Params = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const item of Array.isArray(v) ? v : [v]) qs.append(k, String(item));
  }
  return qs.size ? `${path}?${qs}` : path;
}

async function getList(params: Params): Promise<TraceList> {
  const res = await demoFetch(url("/v1/traces", params));
  expect(res.status).toBe(200);
  return (await res.json()) as TraceList;
}

async function allPages(params: Params): Promise<ParityQuery["pages"]> {
  const pages: ParityQuery["pages"] = [];
  let cursor: string | null = null;
  do {
    const page: TraceList = await getList({ ...params, ...(cursor ? { cursor } : {}) });
    pages.push({
      trace_ids: page.traces.map((t) => t.trace_id),
      has_next: page.next_cursor !== null,
    });
    cursor = page.next_cursor;
  } while (cursor !== null && pages.length < 1000);
  return pages;
}

async function getFacets(params: Params): Promise<TraceFacets> {
  const res = await demoFetch(url("/v1/traces/facets", params));
  expect(res.status).toBe(200);
  return (await res.json()) as TraceFacets;
}

const ids = async (params: Params) => (await allPages(params)).flatMap((p) => p.trace_ids);
const byId = new Map(snapshot.traces.map((t) => [t.trace_id, t]));

describe("demo adapter parity with the real API", () => {
  it.each(parity.queries.map((q) => [JSON.stringify(q.params), q] as const))(
    "pages %s like the API",
    async (_label, query) => {
      expect(await allPages(query.params)).toEqual(query.pages);
    },
  );

  const facetQueries = parity.facets ?? [];
  if (facetQueries.length > 0) {
    it.each(facetQueries.map((q) => [JSON.stringify(q.params), q] as const))(
      "counts facets %s like the API",
      async (_label, query) => {
        expect(await getFacets(query.params)).toEqual(query.facets);
      },
    );
  }

  it("returns each trace's detail from the snapshot", async () => {
    for (const trace of snapshot.traces.slice(0, 20)) {
      const res = await demoFetch(`/v1/traces/${trace.trace_id}`);
      const body = (await res.json()) as TraceDetail;
      expect(body.trace).toEqual(trace);
      expect(body.spans).toEqual(snapshot.spans[trace.trace_id]);
      expect(Number.isNaN(Date.parse(body.as_of))).toBe(false);
    }
  });

  it("404s an unknown trace and 422s bad params like the API", async () => {
    expect((await demoFetch("/v1/traces/0000")).status).toBe(404);
    expect((await demoFetch("/v1/traces?limit=0")).status).toBe(422);
    expect((await demoFetch("/v1/traces?limit=201")).status).toBe(422);
    expect((await demoFetch("/v1/traces?status=weird")).status).toBe(422);
    expect((await demoFetch("/v1/traces?source=weird")).status).toBe(422);
    expect((await demoFetch("/v1/traces?order=sideways")).status).toBe(422);
    expect((await demoFetch("/v1/traces?cursor=garbage")).status).toBe(422);
    expect((await demoFetch("/v1/traces?from=2026-09-25T10:00:00")).status).toBe(422); // naive
    expect((await demoFetch("/v1/traces/facets?to=yesterday")).status).toBe(422);
    expect((await demoFetch("/v1/traces?since=2026-09-25T10:00:00Z&cursor=d1.x")).status).toBe(422);
    expect((await demoFetch("/healthz")).status).toBe(200);
  });
});

describe("demo adapter: the full list contract", () => {
  it("windows on start_time: from inclusive, to exclusive", async () => {
    const t = snapshot.traces[10]!;
    const from = t.start_time;
    const to = snapshot.traces[3]!.start_time;
    const got = await ids({ from, to, limit: 200 });
    const want = snapshot.traces
      .filter((x) => Date.parse(x.start_time) >= Date.parse(from))
      .filter((x) => Date.parse(x.start_time) < Date.parse(to))
      .map((x) => x.trace_id);
    expect(got).toEqual(want);
    expect(got).toContain(t.trace_id);
    expect(got).not.toContain(snapshot.traces[3]!.trace_id);
  });

  it("ORs within a filter and ANDs across filters, for every facet", async () => {
    const got = await ids({
      client: ["claude-code", "copilot-cli"],
      model: ["claude-sonnet-5", "gpt-5"],
      limit: 200,
    });
    expect(got.length).toBeGreaterThan(0);
    for (const id of got) {
      const t = byId.get(id)!;
      expect(["claude-code", "copilot-cli"]).toContain(t.client);
      expect(t.models.some((m) => m === "claude-sonnet-5" || m === "gpt-5")).toBe(true);
    }
    const expected = snapshot.traces.filter(
      (t) =>
        (t.client === "claude-code" || t.client === "copilot-cli") &&
        t.models.some((m) => m === "claude-sonnet-5" || m === "gpt-5"),
    );
    expect(got).toHaveLength(expected.length);

    for (const [param, value, field] of [
      ["name", "support-agent.run", "name"],
      ["service", "lucentpad-gateway", "service_name"],
    ] as const) {
      const hits = await ids({ [param]: value, limit: 200 });
      expect(hits.length).toBe(snapshot.traces.filter((t) => t[field] === value).length);
    }
  });

  it("pages oldest first with order=asc, and cursors are bound to order and filters", async () => {
    const asc = await ids({ order: "asc", limit: 7 });
    expect(asc).toEqual([...snapshot.traces].reverse().map((t) => t.trace_id));

    const first = await getList({ order: "asc", limit: 5, status: "ok" });
    const cursor = first.next_cursor!;
    expect(
      (await demoFetch(url("/v1/traces", { order: "asc", cursor, status: "ok" }))).status,
    ).toBe(200);
    expect((await demoFetch(url("/v1/traces", { cursor, status: "ok" }))).status).toBe(422);
    expect((await demoFetch(url("/v1/traces", { order: "asc", cursor }))).status).toBe(422);
  });

  it("answers since with traces that gained spans after it", async () => {
    const newest = snapshot.traces[0]!;
    const end = Date.parse(newest.start_time) + newest.duration_ms;
    const since = new Date(end - 1).toISOString();
    const live = await getList({ since, limit: 200 });
    expect(live.traces.map((t) => t.trace_id)).toContain(newest.trace_id);
    expect(live.next_cursor).toBeNull();
    const none = await getList({ since: new Date(end + 1000).toISOString() });
    expect(none.traces).toEqual([]);

    const spans = snapshot.spans[newest.trace_id]!;
    const lastEnd = Math.max(...spans.map((s) => Date.parse(s.end_time)));
    const res = await demoFetch(
      url(`/v1/traces/${newest.trace_id}`, { since: new Date(lastEnd - 1).toISOString() }),
    );
    const detail = (await res.json()) as TraceDetail;
    expect(detail.spans.length).toBeGreaterThan(0);
    expect(detail.spans.length).toBeLessThanOrEqual(spans.length);
    expect(detail.trace).toEqual(newest);
  });
});

describe("demo adapter: facets", () => {
  it("counts each facet over the window, excluding its own filter, count desc then value", async () => {
    const all = await getFacets({});
    expect(Object.keys(all).sort()).toEqual([...FACETS].sort());
    const total = (xs: { count: number }[]) => xs.reduce((n, x) => n + x.count, 0);
    expect(total(all.status)).toBe(snapshot.traces.length);
    expect(total(all.source)).toBe(snapshot.traces.length);
    for (const facet of FACETS) {
      const counts = all[facet];
      for (let i = 1; i < counts.length; i++) {
        const [a, b] = [counts[i - 1]!, counts[i]!];
        expect(a.count > b.count || (a.count === b.count && a.value < b.value)).toBe(true);
      }
      expect(counts.length).toBeLessThanOrEqual(50);
    }

    // Own filter excluded: ticking status=error keeps every status visible,
    // but narrows the other facets.
    const filtered = await getFacets({ status: "error" });
    expect(filtered.status).toEqual(all.status);
    const errors = snapshot.traces.filter((t) => t.status === "error");
    expect(total(filtered.source)).toBe(errors.length);

    // model counts traces that used it, not model occurrences.
    const sonnet = all.model.find((m) => m.value === "claude-sonnet-5")!;
    expect(sonnet.count).toBe(
      snapshot.traces.filter((t) => t.models.includes("claude-sonnet-5")).length,
    );
  });

  it("applies the time window", async () => {
    const from = snapshot.traces[9]!.start_time;
    const facets = await getFacets({ from });
    const n = snapshot.traces.filter((t) => Date.parse(t.start_time) >= Date.parse(from)).length;
    expect(facets.status.reduce((s, x) => s + x.count, 0)).toBe(n);
  });
});

describe("fresh shift", () => {
  it("makes the newest trace end a minute before now and keeps order", () => {
    const now = Date.parse("2030-01-01T00:00:00Z");
    const shifted = shiftSnapshot(snapshot, freshShift(snapshot, now));
    const ends = shifted.traces.map((t) => Date.parse(t.start_time) + t.duration_ms);
    expect(Math.abs(Math.max(...ends) - (now - 60_000))).toBeLessThanOrEqual(1);
    expect(shifted.traces.map((t) => t.trace_id)).toEqual(snapshot.traces.map((t) => t.trace_id));
    const first = shifted.traces[0]!;
    const span = shifted.spans[first.trace_id]![0]!;
    expect(Date.parse(span.start_time)).toBeGreaterThan(Date.parse("2029-12-01T00:00:00Z"));
  });

  it("keeps the default 15-minute range non-empty", async () => {
    const now = Date.now();
    const shifted = createDemoFetch(shiftSnapshot(snapshot, freshShift(snapshot, now)));
    const from = new Date(now - 15 * 60_000).toISOString();
    const res = await shifted(url("/v1/traces", { from }));
    const body = (await res.json()) as TraceList;
    expect(body.traces.length).toBeGreaterThan(0);
  });
});
