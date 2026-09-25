import { FACETS, type TraceDetail, type TraceFacets, type TraceList } from "../api/types";
import {
  compareCodePoints,
  createDemoFetch,
  createLiveDemoFetch,
  freshShift,
  shiftSnapshot,
  type DemoSnapshot,
} from "./adapter";
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

describe("demo adapter: sort", () => {
  type Row = (typeof snapshot.traces)[number];
  const cp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0); // ASCII ids and names
  const keys: Record<string, (a: Row, b: Row) => number> = {
    duration: (a, b) => a.duration_ms - b.duration_ms,
    cost: (a, b) => a.cost_usd - b.cost_usd,
    name: (a, b) => cp(a.name, b.name),
    source: (a, b) => cp(a.source, b.source),
  };

  it.each(
    Object.keys(keys).flatMap((sort) => [
      [sort, "desc"],
      [sort, "asc"],
    ]),
  )("sorts by %s %s, ties on trace_id in the same direction, across pages", async (sort, order) => {
    const sign = order === "desc" ? -1 : 1;
    const want = [...snapshot.traces]
      .sort((a, b) => sign * (keys[sort]!(a, b) || cp(a.trace_id, b.trace_id)))
      .map((t) => t.trace_id);
    expect(await ids({ sort, order, limit: 13 })).toEqual(want);
  });

  it("has real ties to break (same name / source)", () => {
    expect(new Set(snapshot.traces.map((t) => t.source)).size).toBeLessThan(snapshot.traces.length);
  });

  it("defaults to started, and sort=started is the snapshot order", async () => {
    const all = snapshot.traces.map((t) => t.trace_id);
    expect(await ids({ limit: 50 })).toEqual(all);
    expect(await ids({ sort: "started", limit: 50 })).toEqual(all);
    expect(await ids({ sort: "started", order: "asc", limit: 50 })).toEqual([...all].reverse());
  });

  it("filters and windows apply under every sort", async () => {
    const got = await ids({ sort: "cost", status: "error", limit: 200 });
    expect(got.length).toBe(snapshot.traces.filter((t) => t.status === "error").length);
    for (const id of got) expect(byId.get(id)!.status).toBe("error");
  });

  it("binds cursors to the sort, and 422s an unknown sort", async () => {
    expect((await demoFetch("/v1/traces?sort=tokens")).status).toBe(422);
    const first = await getList({ sort: "duration", limit: 5 });
    const cursor = first.next_cursor!;
    expect((await demoFetch(url("/v1/traces", { sort: "duration", cursor }))).status).toBe(200);
    expect((await demoFetch(url("/v1/traces", { sort: "cost", cursor }))).status).toBe(422);
    expect((await demoFetch(url("/v1/traces", { cursor }))).status).toBe(422); // started
    expect(
      (await demoFetch(url("/v1/traces", { sort: "duration", order: "asc", cursor }))).status,
    ).toBe(422);
    const started = (await getList({ limit: 5 })).next_cursor!;
    expect((await demoFetch(url("/v1/traces", { sort: "name", cursor: started }))).status).toBe(
      422,
    );
  });

  it('compares strings by code point, like COLLATE "C"', () => {
    expect(compareCodePoints("B", "a")).toBeLessThan(0); // uppercase first
    expect(compareCodePoints("a", "ab")).toBeLessThan(0);
    expect(compareCodePoints("abc", "abc")).toBe(0);
    // U+FF5E (BMP) vs U+1F600 (astral): UTF-16 units put the emoji first (surrogate 0xD83D < 0xFF5E); code points put it last.
    expect(compareCodePoints("\uFF5E", "\u{1F600}")).toBeLessThan(0);
    expect(compareCodePoints("x\u{1F600}a", "x\u{1F600}b")).toBeLessThan(0);
  });

  it("keeps sorted paging working through the clock-following wrapper", async () => {
    let clock = Date.parse("2031-03-01T09:00:00Z");
    const live = createLiveDemoFetch(snapshot, () => clock);
    const got: string[] = [];
    let cursor: string | null = null;
    do {
      const qs = new URLSearchParams({ sort: "name", order: "asc", limit: "40" });
      if (cursor) qs.set("cursor", cursor);
      const res = await live(`/v1/traces?${qs}`);
      expect(res.status).toBe(200);
      const page = (await res.json()) as TraceList;
      got.push(...page.traces.map((t) => t.trace_id));
      cursor = page.next_cursor;
      clock += 30_000;
    } while (cursor !== null);
    expect(got).toEqual(await ids({ sort: "name", order: "asc", limit: 200 }));
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

describe("demo adapter following the clock", () => {
  const newestEnd = Math.max(
    ...snapshot.traces.map((t) => Date.parse(t.start_time) + t.duration_ms),
  );
  const windowIds = async (
    fetcher: (url: string) => Promise<Response>,
    now: number,
    minutes: number,
  ): Promise<string[]> => {
    const ids: string[] = [];
    let cursor: string | null = null;
    do {
      const qs = new URLSearchParams({
        from: new Date(now - minutes * 60_000).toISOString(),
        limit: "20",
        ...(cursor ? { cursor } : {}),
      });
      const page = (await (await fetcher(`/v1/traces?${qs}`)).json()) as TraceList;
      ids.push(...page.traces.map((t) => t.trace_id));
      cursor = page.next_cursor;
    } while (cursor !== null);
    return ids;
  };

  it("shows the same traces for a range at any time, with times relative to now", async () => {
    let clock = Date.parse("2031-03-01T09:00:00Z");
    const live = createLiveDemoFetch(snapshot, () => clock);
    const at9 = {
      m15: await windowIds(live, clock, 15),
      h24: await windowIds(live, clock, 24 * 60),
    };
    // Hours later (a tab left open, or a new visit): same results.
    clock += 5 * 3600_000 + 1234;
    expect(await windowIds(live, clock, 15)).toEqual(at9.m15);
    expect(await windowIds(live, clock, 24 * 60)).toEqual(at9.h24);
    expect(at9.h24.length).toBeGreaterThan(at9.m15.length);

    // The newest trace always ended a minute before "now".
    const page = (await (await live("/v1/traces?limit=1")).json()) as TraceList;
    const newest = page.traces[0]!;
    expect(Date.parse(newest.start_time) + newest.duration_ms).toBeCloseTo(clock - 60_000, -1);
    expect(Date.parse(page.as_of)).toBe(clock);
  });

  it("keeps paging consistent while the clock moves between pages", async () => {
    let clock = Date.parse("2031-03-01T09:00:00Z");
    const live = createLiveDemoFetch(snapshot, () => clock);
    const from = new Date(clock - 7 * 86_400_000).toISOString();
    const first = (await (await live(`/v1/traces?from=${from}&limit=30`)).json()) as TraceList;
    clock += 45_000; // 45 s pass before the user loads more
    const res = await live(`/v1/traces?from=${from}&limit=30&cursor=${first.next_cursor!}`);
    expect(res.status).toBe(200);
    const second = (await res.json()) as TraceList;
    const seen = new Set(first.traces.map((t) => t.trace_id));
    expect(second.traces.some((t) => seen.has(t.trace_id))).toBe(false);
  });

  it("shifts trace detail times the same way", async () => {
    const clock = Date.parse("2031-03-01T09:00:00Z");
    const live = createLiveDemoFetch(snapshot, () => clock);
    const newest = snapshot.traces[0]!;
    const detail = (await (await live(`/v1/traces/${newest.trace_id}`)).json()) as TraceDetail;
    const shift = Math.round(clock - 60_000 - newestEnd);
    expect(Date.parse(detail.trace.start_time)).toBe(Date.parse(newest.start_time) + shift);
    expect(Date.parse(detail.spans[0]!.start_time)).toBe(
      Date.parse(snapshot.spans[newest.trace_id]![0]!.start_time) + shift,
    );
  });
});
