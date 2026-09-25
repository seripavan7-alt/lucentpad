import { describe, expect, it } from "vitest";
import { nextPollDelay } from "../../lib/usePolling";
import { mergeLivePage, mergeSpans, type TracePage } from "../../api/queries";
import { blockedSummary, supportSpans, supportSummary } from "../../test/fixtures";
import {
  applyPatch,
  facetRows,
  filterParams,
  nextSort,
  parseView,
  prependsLive,
  rangeFrom,
  rangePhrase,
} from "./view";

describe("traces view state", () => {
  it("parses defaults and drops invalid values", () => {
    const view = parseView(
      new URLSearchParams("range=2y&order=up&status=bogus&status=error&name="),
    );
    expect(view.range).toBe("24h");
    expect(view.order).toBe("desc");
    expect(view.filters.status).toEqual(["error"]);
    expect(view.filters.name).toEqual([]);
  });

  it("writes patches, omitting defaults", () => {
    const next = applyPatch(new URLSearchParams("span=x&range=1h"), {
      range: "24h",
      order: "asc",
      filters: { model: ["a", "b", "a"] },
    });
    expect(next.toString()).toBe("span=x&order=asc&model=a&model=b");
    expect(applyPatch(next, { order: "desc", filters: { model: [] } }).toString()).toBe("span=x");
  });

  it("parses and writes the sort, omitting started", () => {
    expect(parseView(new URLSearchParams("")).sort).toBe("started");
    expect(parseView(new URLSearchParams("sort=tokens")).sort).toBe("started");
    expect(parseView(new URLSearchParams("sort=cost&order=asc"))).toMatchObject({
      sort: "cost",
      order: "asc",
    });
    const next = applyPatch(new URLSearchParams("range=1h"), { sort: "cost", order: "desc" });
    expect(next.toString()).toBe("range=1h&sort=cost");
    expect(applyPatch(next, { sort: "started" }).toString()).toBe("range=1h");
  });

  it("flips the active column, or switches with a first direction per column", () => {
    const at = (sort: "started" | "name", order: "asc" | "desc") => ({ sort, order });
    expect(nextSort(at("started", "desc"), "started")).toEqual(at("started", "asc"));
    expect(nextSort(at("name", "asc"), "name")).toEqual(at("name", "desc"));
    expect(nextSort(at("started", "asc"), "duration")).toEqual({ sort: "duration", order: "desc" });
    expect(nextSort(at("started", "desc"), "cost")).toEqual({ sort: "cost", order: "desc" });
    expect(nextSort(at("started", "desc"), "name")).toEqual({ sort: "name", order: "asc" });
    expect(nextSort(at("started", "desc"), "source")).toEqual({ sort: "source", order: "asc" });
    expect(nextSort(at("name", "asc"), "started")).toEqual(at("started", "desc"));
  });

  it("prepends live traces only when sorted by Started, newest first", () => {
    expect(prependsLive({ sort: "started", order: "desc" })).toBe(true);
    expect(prependsLive({ sort: "started", order: "asc" })).toBe(false);
    expect(prependsLive({ sort: "cost", order: "desc" })).toBe(false);
  });

  it("builds sorted, non-empty filter params and range windows", () => {
    const view = parseView(new URLSearchParams("name=b&name=a&source=sdk"));
    expect(filterParams(view.filters)).toEqual({ name: ["a", "b"], source: ["sdk"] });
    expect(rangeFrom("1h", Date.parse("2026-09-25T12:00:00Z"))).toBe("2026-09-25T11:00:00.000Z");
    expect(rangePhrase("1h")).toBe("the last hour");
    expect(rangePhrase("7d")).toBe("the last 7 days");
  });

  it("adds selected values missing from the facet at count 0", () => {
    expect(facetRows([{ value: "b", count: 2 }], ["z", "b"])).toEqual([
      { value: "b", count: 2 },
      { value: "z", count: 0 },
    ]);
  });
});

describe("live merging", () => {
  const page = (traces: TracePage["traces"]): TracePage => ({
    traces,
    next_cursor: null,
    as_of: "2026-09-25T10:00:00Z",
    from: "2026-09-25T09:45:00Z",
  });

  it("updates known traces in place and prepends new ones only when asked", () => {
    const data = { pages: [page([supportSummary])], pageParams: [null] };
    const update = {
      traces: [blockedSummary, { ...supportSummary, duration_ms: 1 }],
      next_cursor: null,
      as_of: "2026-09-25T10:00:03Z",
    };
    const desc = mergeLivePage(data, update, true);
    expect(desc.added).toEqual([blockedSummary.trace_id]);
    expect(desc.data.pages[0]!.traces.map((t) => t.trace_id)).toEqual([
      blockedSummary.trace_id,
      supportSummary.trace_id,
    ]);
    expect(desc.data.pages[0]!.traces[1]!.duration_ms).toBe(1);
    expect(desc.data.pages[0]!.as_of).toBe("2026-09-25T10:00:03Z");
    expect(desc.data.pages[0]!.from).toBe("2026-09-25T09:45:00Z");

    const asc = mergeLivePage(data, update, false);
    expect(asc.added).toEqual([]);
    expect(asc.data.pages[0]!.traces).toHaveLength(1);
  });

  it("merges spans by span_id", () => {
    const [a, b] = supportSpans;
    expect(mergeSpans([a!], [a!]).changed).toBe(false);
    const grown = mergeSpans([a!], [b!, { ...a!, status: "error" }]);
    expect(grown.changed).toBe(true);
    expect(grown.spans.map((s) => [s.span_id, s.status])).toEqual([
      [a!.span_id, "error"],
      [b!.span_id, "ok"],
    ]);
  });

  it("backs off exponentially, capped at 30 s", () => {
    expect([0, 1, 2, 3, 4].map((f) => nextPollDelay(3000, f))).toEqual([
      3000, 6000, 12_000, 24_000, 30_000,
    ]);
    expect(nextPollDelay(1000, 10)).toBe(30_000);
  });
});
