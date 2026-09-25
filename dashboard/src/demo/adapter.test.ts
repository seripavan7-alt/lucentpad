import type { TraceDetail, TraceList } from "../api/types";
import { createDemoFetch, freshShift, shiftSnapshot, type DemoSnapshot } from "./adapter";
import parityJson from "./parity.json";
import snapshotJson from "./snapshot.json";

const snapshot = snapshotJson as unknown as DemoSnapshot;

interface ParityQuery {
  params: Record<string, string | number>;
  pages: { trace_ids: string[]; has_next: boolean }[];
}
const parity = parityJson as { queries: ParityQuery[] };

const demoFetch = createDemoFetch(snapshot);

function url(path: string, params: Record<string, string | number> = {}): string {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  return qs.size ? `${path}?${qs}` : path;
}

async function getList(params: Record<string, string | number>): Promise<TraceList> {
  const res = await demoFetch(url("/v1/traces", params));
  expect(res.status).toBe(200);
  return (await res.json()) as TraceList;
}

describe("demo adapter parity with the real API", () => {
  it.each(parity.queries.map((q) => [JSON.stringify(q.params), q] as const))(
    "pages %s like the API",
    async (_label, query) => {
      const pages: ParityQuery["pages"] = [];
      let cursor: string | null = null;
      do {
        const page: TraceList = await getList({
          ...query.params,
          ...(cursor ? { cursor } : {}),
        });
        pages.push({
          trace_ids: page.traces.map((t) => t.trace_id),
          has_next: page.next_cursor !== null,
        });
        cursor = page.next_cursor;
      } while (cursor !== null && pages.length < 1000);
      expect(pages).toEqual(query.pages);
    },
  );

  it("returns each trace's detail from the snapshot", async () => {
    for (const trace of snapshot.traces.slice(0, 20)) {
      const res = await demoFetch(`/v1/traces/${trace.trace_id}`);
      const body = (await res.json()) as TraceDetail;
      expect(body.trace).toEqual(trace);
      expect(body.spans).toEqual(snapshot.spans[trace.trace_id]);
    }
  });

  it("404s an unknown trace and 422s bad params like the API", async () => {
    expect((await demoFetch("/v1/traces/0000")).status).toBe(404);
    expect((await demoFetch("/v1/traces?limit=0")).status).toBe(422);
    expect((await demoFetch("/v1/traces?limit=201")).status).toBe(422);
    expect((await demoFetch("/v1/traces?status=weird")).status).toBe(422);
    expect((await demoFetch("/v1/traces?cursor=garbage")).status).toBe(422);
    expect((await demoFetch("/healthz")).status).toBe(200);
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
});
