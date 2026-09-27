import type { GatewaySummary, GatewayTurnList, Span } from "../api/types";
import { Attr, EventName } from "../api/types";
import { isoMicros } from "../lib/time";
import { createDemoFetch, createLiveDemoFetch, toGatewayTurn, type DemoSnapshot } from "./adapter";
import parityJson from "./parity.json";
import snapshotJson from "./snapshot.json";

const snapshot = snapshotJson as unknown as DemoSnapshot;

type Params = Record<string, string | number | readonly (string | number)[]>;

interface GatewayParityQuery {
  params: Params;
  pages: { span_ids: string[]; has_next: boolean }[];
}
interface GatewaySummaryParity {
  params: Params;
  summary: Omit<GatewaySummary, "as_of">;
}
const parity = parityJson as unknown as {
  gateway_turns?: GatewayParityQuery[];
  gateway_summary?: GatewaySummaryParity[];
};

const demoFetch = createDemoFetch(snapshot);

function url(path: string, params: Params = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const item of Array.isArray(v) ? v : [v]) qs.append(k, String(item));
  }
  return qs.size ? `${path}?${qs}` : path;
}

type Fetcher = (url: string) => Promise<Response>;

async function getTurns(params: Params, fetcher: Fetcher = demoFetch): Promise<GatewayTurnList> {
  const res = await fetcher(url("/v1/gateway/turns", params));
  expect(res.status).toBe(200);
  return (await res.json()) as GatewayTurnList;
}

async function allPages(params: Params, fetcher: Fetcher = demoFetch) {
  const pages: GatewayTurnList[] = [];
  let cursor: string | null = null;
  do {
    const page: GatewayTurnList = await getTurns(
      { ...params, ...(cursor ? { cursor } : {}) },
      fetcher,
    );
    pages.push(page);
    cursor = page.next_cursor;
  } while (cursor !== null && pages.length < 1000);
  return pages;
}

async function getSummary(params: Params, fetcher: Fetcher = demoFetch): Promise<GatewaySummary> {
  const res = await fetcher(url("/v1/gateway/summary", params));
  expect(res.status).toBe(200);
  return (await res.json()) as GatewaySummary;
}

const gatewaySpans: Span[] = Object.values(snapshot.spans)
  .flat()
  .filter((s) => s.source === "gateway" && s.kind === "llm");

describe("demo adapter: gateway parity with the real API", () => {
  const turnQueries = parity.gateway_turns ?? [];
  const summaryQueries = parity.gateway_summary ?? [];

  it("has parity fixtures for both endpoints", () => {
    expect(turnQueries.length).toBeGreaterThan(0);
    expect(summaryQueries.length).toBeGreaterThan(0);
  });

  it.each(turnQueries.map((q) => [JSON.stringify(q.params), q] as const))(
    "pages turns %s like the API",
    async (_label, query) => {
      const pages = await allPages(query.params);
      expect(
        pages.map((p) => ({
          span_ids: p.turns.map((t) => t.span_id),
          has_next: p.next_cursor !== null,
        })),
      ).toEqual(query.pages);
    },
  );

  it.each(summaryQueries.map((q) => [JSON.stringify(q.params), q] as const))(
    "sums %s like the API",
    async (_label, query) => {
      const summary = await getSummary(query.params);
      expect({ clients: summary.clients }).toEqual(query.summary);
    },
  );
});

describe("demo adapter: gateway turns", () => {
  it("lists every gateway llm span, newest first by (start, span_id, trace_id)", async () => {
    const turns = (await allPages({ limit: 200 })).flatMap((p) => p.turns);
    expect(turns).toHaveLength(gatewaySpans.length);
    for (let i = 1; i < turns.length; i++) {
      const a = turns[i - 1]!;
      const b = turns[i]!;
      const d = isoMicros(a.start_time) - isoMicros(b.start_time);
      expect(d > 0 || (d === 0 && a.span_id > b.span_id)).toBe(true);
    }
  });

  it("maps span attributes onto the turn like the API", () => {
    const span: Span = {
      trace_id: "ab".repeat(16),
      span_id: "cd".repeat(8),
      parent_span_id: null,
      name: "chat gpt-5",
      kind: "llm",
      source: "gateway",
      status: "ok",
      start_time: "2026-09-25T10:00:00.000100Z",
      end_time: "2026-09-25T10:00:01.500600Z",
      attributes: {
        [Attr.GEN_AI_REQUEST_MODEL]: "gpt-5",
        [Attr.GEN_AI_RESPONSE_MODEL]: "gpt-5-mini",
        [Attr.GEN_AI_SYSTEM]: "openai",
        [Attr.GEN_AI_INPUT_TOKENS]: 1200,
        [Attr.GEN_AI_OUTPUT_TOKENS]: 80,
        [Attr.COST_USD]: 0.00046,
        [Attr.CLIENT]: "copilot-chat",
        [Attr.TTFB_MS]: 212.5,
        [Attr.STREAMING]: true,
        [Attr.INPUT_PREVIEW]: "hi",
      },
      events: [{ name: EventName.FAILOVER, time: "2026-09-25T10:00:01Z", attributes: {} }],
    };
    expect(toGatewayTurn(span)).toEqual({
      trace_id: span.trace_id,
      span_id: span.span_id,
      client: "copilot-chat",
      provider: "openai",
      model: "gpt-5-mini",
      start_time: span.start_time,
      duration_ms: 1500.5,
      ttfb_ms: 212.5,
      status: "ok",
      streaming: true,
      input_tokens: 1200,
      output_tokens: 80,
      cost_usd: 0.00046,
      failover: true,
      input_preview: "hi",
      output_preview: null,
    });
    const bare = toGatewayTurn({
      ...span,
      attributes: { [Attr.GATEWAY_UPSTREAM]: "anthropic", [Attr.GEN_AI_SYSTEM]: "openai" },
      events: [],
    });
    expect(bare).toMatchObject({
      provider: "anthropic", // the gateway's upstream wins over gen_ai.system
      model: null,
      client: null,
      ttfb_ms: null,
      streaming: false,
      cost_usd: null,
      failover: false,
    });
  });

  it("ORs clients, windows [from, to) on start, and binds cursors to the filters", async () => {
    const both = (await allPages({ client: ["copilot-cli", "copilot-chat"], limit: 200 })).flatMap(
      (p) => p.turns,
    );
    const want = gatewaySpans.filter((s) =>
      ["copilot-cli", "copilot-chat"].includes(s.attributes?.[Attr.CLIENT] as string),
    );
    expect(both).toHaveLength(want.length);

    const pivot = both[10]!;
    const from = new Date(Date.parse(pivot.start_time)).toISOString();
    const windowed = (await allPages({ from, limit: 200 })).flatMap((p) => p.turns);
    expect(windowed.every((t) => isoMicros(t.start_time) >= Date.parse(from) * 1000)).toBe(true);

    const first = await getTurns({ client: "claude-code", limit: 5 });
    const cursor = first.next_cursor!;
    expect(
      (await demoFetch(url("/v1/gateway/turns", { client: "claude-code", cursor }))).status,
    ).toBe(200);
    expect((await demoFetch(url("/v1/gateway/turns", { cursor }))).status).toBe(422);
    expect((await demoFetch(url("/v1/gateway/turns", { cursor: "nope" }))).status).toBe(422);
  });

  it("validates params like the API", async () => {
    const since = new Date().toISOString();
    for (const params of [
      { limit: 0 },
      { limit: 201 },
      { from: "2026-09-25T10:00:00" }, // naive
      { since, cursor: "g1.x" },
    ] as Params[]) {
      expect((await demoFetch(url("/v1/gateway/turns", params))).status).toBe(422);
    }
    expect((await demoFetch(url("/v1/gateway/summary", { to: "yesterday" }))).status).toBe(422);
  });

  it("answers since with turns that ended after it, never with a cursor", async () => {
    const newestEnd = Math.max(...gatewaySpans.map((s) => Date.parse(s.end_time)));
    const since = new Date(newestEnd - 3_600_000).toISOString();
    const page = await getTurns({ since, limit: 200 });
    const want = gatewaySpans.filter((s) => Date.parse(s.end_time) > Date.parse(since));
    expect(page.turns).toHaveLength(want.length);
    expect(page.next_cursor).toBeNull();
    expect((await getTurns({ since, limit: 1 })).next_cursor).toBeNull();
  });
});

describe("demo adapter: gateway summary", () => {
  it("totals per client, most expensive first, matching the turns", async () => {
    const { clients } = await getSummary({});
    const costs = clients.map((c) => c.cost_usd);
    expect(costs).toEqual([...costs].sort((a, b) => b - a));
    const turns = (await allPages({ limit: 200 })).flatMap((p) => p.turns);
    for (const c of clients) {
      const mine = turns.filter((t) => (t.client ?? "other") === c.client);
      expect(c.turns).toBe(mine.length);
      expect(c.sessions).toBe(new Set(mine.map((t) => t.trace_id)).size);
      expect(c.input_tokens).toBe(mine.reduce((n, t) => n + (t.input_tokens ?? 0), 0));
    }
  });
});

describe("demo adapter: gateway following the clock", () => {
  it("shifts turn times (microseconds kept) and keeps paging and totals stable", async () => {
    let clock = Date.parse("2031-03-01T09:00:00Z");
    const live = createLiveDemoFetch(snapshot, () => clock);
    const from = () => new Date(clock - 86_400_000).toISOString();

    const day = await allPages({ from: from(), limit: 40 }, live);
    const turns = day.flatMap((p) => p.turns);
    // The static demo's default Gateway view (24h) is never empty.
    expect(turns.length).toBeGreaterThan(0);
    expect(turns.every((t) => Date.parse(t.start_time) >= clock - 86_400_000)).toBe(true);
    expect(turns.every((t) => Date.parse(t.start_time) <= clock)).toBe(true);
    expect(turns[0]!.start_time).toMatch(/\.\d{6}Z$/);
    expect(Date.parse(day[0]!.as_of)).toBe(clock);
    const summary = await getSummary({ from: from() }, live);
    expect(summary.clients.reduce((n, c) => n + c.turns, 0)).toBe(turns.length);
    expect(Date.parse(summary.as_of)).toBe(clock);

    // Hours later the same window shows the same turns, and a cursor keeps its own shift
    // even when the clock moves between pages.
    const firstPage = await getTurns({ from: from(), limit: 40 }, live);
    clock += 3 * 3600_000 + 777;
    const later = await getTurns({ from: from(), limit: 40 }, live);
    expect(later.turns.map((t) => t.span_id)).toEqual(firstPage.turns.map((t) => t.span_id));
    const pageFrom = from();
    const page1 = await getTurns({ from: pageFrom, limit: 40 }, live);
    clock += 45_000;
    const page2 = await getTurns({ from: pageFrom, limit: 40, cursor: page1.next_cursor! }, live);
    const seen = new Set(page1.turns.map((t) => t.span_id));
    expect(page2.turns.length).toBeGreaterThan(0);
    expect(page2.turns.some((t) => seen.has(t.span_id))).toBe(false);
  });
});
