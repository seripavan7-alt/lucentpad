import type {
  CostSeries,
  EvalRun,
  EvalRunList,
  GuardrailEventList,
  GuardrailSummary,
  PriceTable,
  Span,
} from "../api/types";
import { Attr, EventName } from "../api/types";
import { isoMicros } from "../lib/time";
import {
  bucketSeconds,
  createDemoFetch,
  createLiveDemoFetch,
  DEMO_PRICES,
  DEMO_RULES,
  type DemoSnapshot,
} from "./adapter";
import parityJson from "./parity.json";
import snapshotJson from "./snapshot.json";

const snapshot = snapshotJson as unknown as DemoSnapshot;
const demoFetch = createDemoFetch(snapshot);

type ParityParams = Record<string, string | number | string[]>;
/** How the real API answered (server/tests/test_demo_snapshot.py), `as_of` removed. */
const parity = parityJson as unknown as {
  guardrail_events?: {
    params: ParityParams;
    pages: { events: GuardrailEventList["events"]; has_next: boolean }[];
  }[];
  guardrail_summary?: { params: ParityParams; summary: Omit<GuardrailSummary, "as_of"> }[];
  costs?: { params: ParityParams; series: Omit<CostSeries, "as_of"> }[];
  eval_runs?: {
    params: ParityParams;
    pages: { runs: EvalRunList["runs"]; has_next: boolean }[];
  }[];
};

type Params = Record<string, string | number | readonly (string | number)[]>;
type Fetcher = (url: string) => Promise<Response>;

function url(path: string, params: Params = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const item of Array.isArray(v) ? v : [v]) qs.append(k, String(item));
  }
  return qs.size ? `${path}?${qs}` : path;
}

async function get<T>(path: string, params: Params = {}, fetcher: Fetcher = demoFetch): Promise<T> {
  const res = await fetcher(url(path, params));
  expect(res.status).toBe(200);
  return (await res.json()) as T;
}

async function allEvents(params: Params, fetcher: Fetcher = demoFetch) {
  const pages: GuardrailEventList[] = [];
  let cursor: string | null = null;
  do {
    const page: GuardrailEventList = await get(
      "/v1/guardrails/events",
      { ...params, ...(cursor ? { cursor } : {}) },
      fetcher,
    );
    pages.push(page);
    cursor = page.next_cursor;
  } while (cursor !== null && pages.length < 1000);
  return pages;
}

const spans: Span[] = Object.values(snapshot.spans).flat();
const blockSpans = spans.filter((s) => s.kind === "guardrail");
const redactions = spans.flatMap((s) =>
  (s.events ?? []).filter((e) => e.name === EventName.REDACTION).map((e) => ({ span: s, e })),
);
const budgetAlerts = spans.flatMap((s) =>
  (s.events ?? []).filter((e) => e.name === EventName.BUDGET_ALERT).map((e) => ({ span: s, e })),
);
const pricedSpans = spans.filter((s) => typeof s.attributes?.[Attr.COST_USD] === "number");
const UNITS = 1e8;
const sumCost = (list: Span[]) =>
  list.reduce((n, s) => n + Math.round((s.attributes?.[Attr.COST_USD] as number) * UNITS), 0) /
  UNITS;

/** Every page of a list query: its items under `key`, plus whether another page follows. */
async function pagesOf(path: string, key: string, params: Params) {
  const pages: Record<string, unknown>[] = [];
  let cursor: string | null = null;
  do {
    const body: Record<string, unknown> = await get(path, {
      ...params,
      ...(cursor ? { cursor } : {}),
    });
    cursor = body.next_cursor as string | null;
    pages.push({ [key]: body[key], has_next: cursor !== null });
  } while (cursor !== null && pages.length < 1000);
  return pages;
}

/** A body without its (time-dependent) `as_of`. */
function withoutAsOf<T extends { as_of: string }>(body: T): Omit<T, "as_of"> {
  const rest: Partial<T> = { ...body };
  delete rest.as_of;
  return rest as Omit<T, "as_of">;
}

/** Cost points with the cost left out (compared separately, to float precision). */
const shape = (points: CostSeries["points"]) => points.map((p) => ({ ...p, cost_usd: undefined }));

describe("demo adapter: M3 parity with the real API", () => {
  it("has parity fixtures for every M3 list and summary", () => {
    expect(parity.guardrail_events?.length).toBeGreaterThan(0);
    expect(parity.guardrail_summary?.length).toBeGreaterThan(0);
    expect(parity.costs?.length).toBeGreaterThan(0);
    expect(parity.eval_runs?.length).toBeGreaterThan(0);
  });

  it.each((parity.guardrail_events ?? []).map((q) => [JSON.stringify(q.params), q] as const))(
    "pages guardrail events %s like the API",
    async (_label, query) => {
      expect(await pagesOf("/v1/guardrails/events", "events", query.params)).toEqual(query.pages);
    },
  );

  it.each((parity.guardrail_summary ?? []).map((q) => [JSON.stringify(q.params), q] as const))(
    "counts guardrail summary %s like the API",
    async (_label, query) => {
      const summary = await get<GuardrailSummary>("/v1/guardrails/summary", query.params);
      expect(withoutAsOf(summary)).toEqual(query.summary);
    },
  );

  it.each((parity.costs ?? []).map((q) => [JSON.stringify(q.params), q] as const))(
    "buckets costs %s like the API",
    async (_label, query) => {
      const series = await get<CostSeries>("/v1/costs", query.params);
      const { points, total_cost_usd, ...rest } = withoutAsOf(series);
      const { points: want, total_cost_usd: wantTotal, ...wantRest } = query.series;
      expect(rest).toEqual(wantRest);
      expect(total_cost_usd).toBeCloseTo(wantTotal, 8);
      expect(shape(points)).toEqual(shape(want));
      points.forEach((p, i) => {
        expect(p.cost_usd).toBeCloseTo(want[i]!.cost_usd, 8);
      });
    },
  );

  it.each((parity.eval_runs ?? []).map((q) => [JSON.stringify(q.params), q] as const))(
    "pages eval runs %s like the API",
    async (_label, query) => {
      expect(await pagesOf("/v1/evals/runs", "runs", query.params)).toEqual(query.pages);
    },
  );

  it("serves every snapshot eval run in full, and the snapshot's rules", async () => {
    for (const run of snapshot.eval_runs ?? []) {
      expect(await get<EvalRun>(`/v1/evals/runs/${run.id}`)).toEqual(run);
    }
    expect(await get("/v1/guardrails/rules")).toEqual(snapshot.guardrail_rules ?? DEMO_RULES);
  });
});

describe("demo adapter: pricing", () => {
  it("serves the server's price table, sorted by model", async () => {
    const prices = await get<PriceTable>("/v1/pricing");
    expect(prices).toEqual(DEMO_PRICES);
    expect(prices.prices.map((p) => p.model)).toEqual(
      [...prices.prices.map((p) => p.model)].sort(),
    );
  });

  it("falls back to the built-in rules for an older snapshot", async () => {
    const old = createDemoFetch({ ...snapshot, guardrail_rules: undefined });
    expect(await get("/v1/guardrails/rules", {}, old)).toEqual(DEMO_RULES);
  });
});

describe("demo adapter: guardrail events", () => {
  it("lists every block span, redaction and budget alert event, newest first", async () => {
    const events = (await allEvents({ limit: 200 })).flatMap((p) => p.events);
    expect(budgetAlerts.length).toBeGreaterThan(0);
    expect(events).toHaveLength(blockSpans.length + redactions.length + budgetAlerts.length);
    expect(events.filter((e) => e.kind === "block")).toHaveLength(blockSpans.length);
    for (let i = 1; i < events.length; i++) {
      expect(isoMicros(events[i - 1]!.time)).toBeGreaterThanOrEqual(isoMicros(events[i]!.time));
    }
    const block = events.find((e) => e.kind === "block")!;
    const span = blockSpans.find((s) => s.span_id === block.span_id)!;
    expect(block).toMatchObject({
      trace_id: span.trace_id,
      time: span.start_time,
      source: span.source,
      rule: span.attributes?.[Attr.GUARDRAIL_RULE],
      count: 1,
      redaction_kind: null,
    });
    expect(block.reason).toBeTruthy();
    const red = events.find((e) => e.kind === "redaction")!;
    expect(red.redaction_kind).toBe("email");
    expect(red.count).toBeGreaterThan(0);
    expect(red.rule).toBeNull();
    expect(red.budget_limit_usd).toBeNull();
  });

  it("reads budget alerts from their span event, filters by kind, and counts them", async () => {
    const budget = (await allEvents({ kind: "budget", limit: 200 })).flatMap((p) => p.events);
    expect(budget).toHaveLength(budgetAlerts.length);
    const { span, e } = budgetAlerts[0]!;
    const row = budget.find((b) => b.span_id === span.span_id && b.time === e.time)!;
    expect(row).toMatchObject({
      kind: "budget",
      trace_id: span.trace_id,
      source: span.source,
      count: 1,
      rule: null,
      redaction_kind: null,
      budget_limit_usd: e.attributes?.[Attr.BUDGET_LIMIT_USD],
      budget_spent_usd: e.attributes?.[Attr.BUDGET_SPENT_USD],
    });
    // Two of three kinds is a real filter; all three is the same query (and cursor) as none.
    const two = (await allEvents({ kind: ["block", "redaction"], limit: 200 })).flatMap(
      (p) => p.events,
    );
    expect(two).toHaveLength(blockSpans.length + redactions.length);
    const none = await get<GuardrailEventList>("/v1/guardrails/events", { limit: 3 });
    const every = ["block", "redaction", "budget"];
    expect(
      (await demoFetch(url("/v1/guardrails/events", { kind: every, cursor: none.next_cursor! })))
        .status,
    ).toBe(200);
    const summary = await get<GuardrailSummary>("/v1/guardrails/summary");
    expect(summary.budget_alerts).toBe(budgetAlerts.length);
  });

  it("pages with cursors bound to the filters and filters by kind and window", async () => {
    const all = (await allEvents({ limit: 200 })).flatMap((p) => p.events);
    const paged = await allEvents({ limit: 7 });
    expect(paged.flatMap((p) => p.events)).toEqual(all);
    expect(paged.at(-1)!.next_cursor).toBeNull();

    const blocks = (await allEvents({ kind: "block" })).flatMap((p) => p.events);
    expect(blocks.every((e) => e.kind === "block")).toBe(true);
    expect(blocks).toHaveLength(blockSpans.length);

    const pivot = all[20]!;
    const from = new Date(Date.parse(pivot.time)).toISOString();
    const windowed = (await allEvents({ from, limit: 200 })).flatMap((p) => p.events);
    expect(windowed.every((e) => isoMicros(e.time) >= Date.parse(from) * 1000)).toBe(true);
    expect(windowed.length).toBeGreaterThanOrEqual(20);

    const first = await get<GuardrailEventList>("/v1/guardrails/events", { limit: 3 });
    const cursor = first.next_cursor!;
    expect((await demoFetch(url("/v1/guardrails/events", { limit: 3, cursor }))).status).toBe(200);
    expect((await demoFetch(url("/v1/guardrails/events", { kind: "block", cursor }))).status).toBe(
      422,
    );
  });

  it("validates params like the API", async () => {
    const since = new Date().toISOString();
    for (const params of [
      { limit: 0 },
      { limit: 201 },
      { kind: "nope" },
      { from: "2026-09-25T10:00:00" },
      { since, cursor: "e1.x" },
      { cursor: "junk" },
    ] as Params[]) {
      expect((await demoFetch(url("/v1/guardrails/events", params))).status).toBe(422);
    }
    expect((await demoFetch(url("/v1/guardrails/summary", { to: "later" }))).status).toBe(422);
  });

  it("answers since with events whose span ended after it, never with a cursor", async () => {
    const newest = Math.max(...blockSpans.map((s) => Date.parse(s.end_time)));
    const since = new Date(newest - 1).toISOString();
    const page = await get<GuardrailEventList>("/v1/guardrails/events", { since, limit: 200 });
    expect(page.events.length).toBeGreaterThan(0);
    expect(page.next_cursor).toBeNull();
  });

  it("sums blocks per rule and redacted values per kind, matching the events", async () => {
    const summary = await get<GuardrailSummary>("/v1/guardrails/summary");
    const events = (await allEvents({ limit: 200 })).flatMap((p) => p.events);
    expect(summary.blocks.reduce((n, b) => n + b.blocks, 0)).toBe(blockSpans.length);
    expect(summary.redactions.reduce((n, r) => n + r.count, 0)).toBe(
      events.filter((e) => e.kind === "redaction").reduce((n, e) => n + e.count, 0),
    );
    const counts = summary.blocks.map((b) => b.blocks);
    expect(counts).toEqual([...counts].sort((a, b) => b - a));
  });
});

describe("demo adapter: costs", () => {
  it("sums every priced llm span, per group, in aligned buckets", async () => {
    for (const group of ["model", "client", "service"] as const) {
      const series = await get<CostSeries>("/v1/costs", { group_by: group });
      expect(series.group_by).toBe(group);
      expect(series.total_cost_usd).toBeCloseTo(sumCost(pricedSpans), 8);
      expect(series.points.reduce((n, p) => n + p.calls, 0)).toBe(pricedSpans.length);
      for (const p of series.points) {
        expect(Date.parse(p.bucket) % (series.bucket_seconds * 1000)).toBe(0);
      }
    }
    const byModel = await get<CostSeries>("/v1/costs");
    const sonnet = byModel.points.filter((p) => p.group === "claude-sonnet-5");
    const want = pricedSpans.filter(
      (s) =>
        (s.attributes?.[Attr.GEN_AI_RESPONSE_MODEL] ??
          s.attributes?.[Attr.GEN_AI_REQUEST_MODEL]) === "claude-sonnet-5",
    );
    expect(sonnet.reduce((n, p) => n + p.calls, 0)).toBe(want.length);
  });

  it("windows on span start and picks the bucket size from the window", async () => {
    const to = Date.parse(snapshot.generated_at);
    const from = new Date(to - 86_400_000).toISOString();
    const day = await get<CostSeries>("/v1/costs", { from, to: new Date(to).toISOString() });
    expect(day.bucket_seconds).toBe(bucketSeconds(86_400_000));
    const want = pricedSpans.filter((s) => Date.parse(s.start_time) >= Date.parse(from));
    expect(day.total_cost_usd).toBeCloseTo(sumCost(want), 8);
    expect(bucketSeconds(2 * 3600_000)).toBe(300);
    expect(bucketSeconds(2 * 86_400_000)).toBe(3600);
    expect(bucketSeconds(30 * 86_400_000)).toBe(86_400);
    expect((await demoFetch(url("/v1/costs", { group_by: "team" }))).status).toBe(422);
  });
});

function withRuns(runs: EvalRun[]): DemoSnapshot {
  return { ...snapshot, eval_runs: runs };
}

/** The newest trace's end: the live demo puts it a minute before the viewer's clock. */
const newestEnd = Math.max(...snapshot.traces.map((t) => Date.parse(t.start_time) + t.duration_ms));

const run = (id: string, minutesAgo: number, over: Partial<EvalRun> = {}): EvalRun => ({
  id,
  suite: "support_agent",
  status: "passed",
  started_at: new Date(newestEnd - minutesAgo * 60_000).toISOString(),
  duration_ms: 1000,
  model: "claude-haiku-4-5",
  git_sha: "abc",
  git_ref: "main",
  ci_url: null,
  cost_usd: 0.01,
  baseline_cost_usd: 0.01,
  cases: [
    {
      case: "a",
      passed: true,
      baseline_passed: true,
      checks: [],
      cost_usd: null,
      latency_ms: null,
      trace_id: null,
      output_preview: null,
    },
    {
      case: "b",
      passed: false,
      baseline_passed: true,
      checks: [],
      cost_usd: null,
      latency_ms: null,
      trace_id: null,
      output_preview: null,
    },
  ],
  ...over,
});

describe("demo adapter: eval runs", () => {
  const runs = [run("r1", 60), run("r2", 5, { suite: "other" }), run("r3", 30)];
  const fetcher = createDemoFetch(withRuns(runs));

  it("lists runs newest first as summaries, pages and filters by suite", async () => {
    const list = await get<EvalRunList>("/v1/evals/runs", {}, fetcher);
    expect(list.runs.map((r) => r.id)).toEqual(["r2", "r3", "r1"]);
    expect(list.runs[0]).toMatchObject({
      passed: 1,
      failed: 1,
      regressions: 1,
      git_ref: "main",
      baseline_cost_usd: 0.01,
    });
    const page1 = await get<EvalRunList>("/v1/evals/runs", { limit: 2 }, fetcher);
    const page2 = await get<EvalRunList>(
      "/v1/evals/runs",
      { limit: 2, cursor: page1.next_cursor! },
      fetcher,
    );
    expect([...page1.runs, ...page2.runs].map((r) => r.id)).toEqual(["r2", "r3", "r1"]);
    expect(page2.next_cursor).toBeNull();
    const suite = await get<EvalRunList>("/v1/evals/runs", { suite: "support_agent" }, fetcher);
    expect(suite.runs.map((r) => r.id)).toEqual(["r3", "r1"]);
    expect(
      (await fetcher(url("/v1/evals/runs", { suite: "other", cursor: page1.next_cursor! }))).status,
    ).toBe(422);
  });

  it("leaves every run out with hide_sample (demo runs are all sample data)", async () => {
    const hidden = await get<EvalRunList>("/v1/evals/runs", { hide_sample: "true" }, fetcher);
    expect(hidden).toEqual({ runs: [], next_cursor: null });
    const shown = await get<EvalRunList>("/v1/evals/runs", { hide_sample: "false" }, fetcher);
    expect(shown.runs).toHaveLength(3);
    const page1 = await get<EvalRunList>("/v1/evals/runs", { limit: 2 }, fetcher);
    const cursor = page1.next_cursor!;
    expect((await fetcher(url("/v1/evals/runs", { hide_sample: "true", cursor }))).status).toBe(
      422,
    );
    expect((await fetcher(url("/v1/evals/runs", { hide_sample: "maybe" }))).status).toBe(422);
  });

  it("returns one run with its cases, or 404", async () => {
    expect(await get<EvalRun>("/v1/evals/runs/r3", {}, fetcher)).toEqual(runs[2]);
    expect((await fetcher("/v1/evals/runs/nope")).status).toBe(404);
  });

  it("answers an empty list when the snapshot has no runs", async () => {
    const empty = createDemoFetch({ ...snapshot, eval_runs: undefined });
    expect(await get<EvalRunList>("/v1/evals/runs", {}, empty)).toEqual({
      runs: [],
      next_cursor: null,
    });
  });
});

describe("demo adapter: M3 endpoints following the clock", () => {
  it("shifts event, bucket and run times to the viewer's clock", async () => {
    const clock = Date.parse("2031-03-01T09:07:13Z");
    const live = createLiveDemoFetch(withRuns([run("r1", 10)]), () => clock);
    const week = new Date(clock - 7 * 86_400_000).toISOString();

    const events = (await allEvents({ from: week, limit: 50 }, live)).flatMap((p) => p.events);
    expect(events.length).toBeGreaterThan(0);
    expect(events.every((e) => Date.parse(e.time) >= clock - 7 * 86_400_000)).toBe(true);
    expect(events.every((e) => Date.parse(e.time) <= clock)).toBe(true);
    const summary = await get<GuardrailSummary>("/v1/guardrails/summary", { from: week }, live);
    expect(summary.blocks.reduce((n, b) => n + b.blocks, 0)).toBe(
      events.filter((e) => e.kind === "block").length,
    );
    expect(summary.budget_alerts).toBe(events.filter((e) => e.kind === "budget").length);
    const month = new Date(clock - 30 * 86_400_000).toISOString();
    const budget = (await allEvents({ from: month, kind: "budget" }, live)).flatMap(
      (p) => p.events,
    );
    expect(budget).toHaveLength(budgetAlerts.length);
    expect(budget.every((e) => Date.parse(e.time) <= clock)).toBe(true);
    expect(Date.parse(summary.as_of)).toBe(clock);

    const day = new Date(clock - 86_400_000).toISOString();
    const series = await get<CostSeries>("/v1/costs", { from: day }, live);
    // The default Costs view (24h) is never empty, and buckets start on round times.
    expect(series.points.length).toBeGreaterThan(0);
    for (const p of series.points) {
      expect(Date.parse(p.bucket) % (series.bucket_seconds * 1000)).toBe(0);
      expect(Date.parse(p.bucket)).toBeGreaterThanOrEqual(
        clock - 86_400_000 - series.bucket_seconds * 1000,
      );
      expect(Date.parse(p.bucket)).toBeLessThanOrEqual(clock);
    }
    expect(series.bucket_seconds).toBe(bucketSeconds(86_400_000));

    const runs = await get<EvalRunList>("/v1/evals/runs", {}, live);
    const started = Date.parse(runs.runs[0]!.started_at);
    expect(started).toBeLessThanOrEqual(clock);
    expect(clock - started).toBeLessThan(86_400_000);
    const detail = await get<EvalRun>("/v1/evals/runs/r1", {}, live);
    expect(detail.started_at).toBe(runs.runs[0]!.started_at);
  });
});
