import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CostSeries, GuardrailEventList } from "../api/types";
import { setHideSamplePreference } from "../lib/samplePreference";
import { SUPPORT_TRACE_ID, supportDetail, traceListPage1 } from "../test/fixtures";
import {
  budgetEvents,
  costsByClient,
  costsByModel,
  emptyCosts,
  M3_NOW,
  noEvents,
  priceTable,
} from "../test/m3Fixtures";
import { getLocation, HttpError, mockFetch, renderApp } from "../test/render";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(M3_NOW);
});

afterEach(() => {
  vi.useRealTimers();
  setHideSamplePreference(false);
});

type Mock = ReturnType<typeof mockFetch>;

function mockCosts({
  costs = (url: URL) =>
    url.searchParams.get("group_by") === "client" ? costsByClient : costsByModel,
  alerts = () => budgetEvents,
  data = { sample_data: true, real_data: false },
}: {
  costs?: (url: URL) => CostSeries | HttpError;
  alerts?: (url: URL) => GuardrailEventList | HttpError;
  data?: object;
} = {}) {
  return mockFetch({
    "/v1/costs": costs,
    "/v1/guardrails/events": alerts,
    "/v1/traces": () => traceListPage1,
    "/v1/traces/:id": () => supportDetail,
    "/v1/pricing": () => priceTable,
    "/v1/data": () => data,
  });
}

const costRequests = (mock: Mock) => mock.urls().filter((u) => u.pathname === "/v1/costs");
const traceRequests = (mock: Mock) => mock.urls().filter((u) => u.pathname === "/v1/traces");
const alertRequests = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/guardrails/events");
const alertRows = () => {
  const section = screen.getByRole("region", { name: /^Budget alerts/ });
  return [...section.querySelectorAll<HTMLElement>("tr[data-event-key]")];
};
const tile = (name: string) =>
  within(screen.getByRole("region", { name: "Totals for the range" })).getByRole("group", { name });
const legend = () => screen.getByRole("list", { name: /^Spend by/ });

describe("Costs page", () => {
  it("shows the range's totals: spend, calls and tokens", async () => {
    mockCosts();
    renderApp("/costs");
    expect(screen.getByRole("heading", { level: 1, name: "Costs" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Totals for the range" })).toBeInTheDocument();
    expect(tile("Spend")).toHaveTextContent("$1.80");
    expect(tile("Spend")).toHaveTextContent("$0.1000 per call");
    expect(tile("LLM calls")).toHaveTextContent("18");
    expect(tile("LLM calls")).toHaveTextContent("across 3 models");
    expect(tile("Tokens")).toHaveTextContent("205k");
    expect(tile("Tokens")).toHaveTextContent("195k in · 10k out");
  });

  it("asks for the last 24 hours by model, and the 10 most expensive traces", async () => {
    const mock = mockCosts();
    renderApp("/costs");
    await screen.findByRole("region", { name: "Totals for the range" });
    const req = costRequests(mock)[0]!;
    expect(Date.parse(req.searchParams.get("from")!)).toBe(M3_NOW - 86_400_000);
    expect(req.searchParams.get("group_by")).toBe("model");
    expect(req.searchParams.has("hide_sample")).toBe(false);
    await waitFor(() => {
      expect(traceRequests(mock).length).toBeGreaterThan(0);
    });
    const traces = traceRequests(mock)[0]!;
    expect(traces.searchParams.get("sort")).toBe("cost");
    expect(traces.searchParams.get("order")).toBe("desc");
    expect(traces.searchParams.get("limit")).toBe("10");
    expect(Date.parse(traces.searchParams.get("from")!)).toBe(M3_NOW - 86_400_000);
  });

  it("lists each group with its total and share, biggest first, unknown as Other", async () => {
    mockCosts();
    renderApp("/costs");
    await screen.findByRole("region", { name: "Totals for the range" });
    const items = within(legend()).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "claude-sonnet-5$1.5083%",
      "claude-haiku-4-5$0.250014%",
      "Other$0.05003%",
    ]);
  });

  it("draws one column per hour of the range, stacked, with the peak labelled", async () => {
    mockCosts();
    renderApp("/costs");
    const chart = await screen.findByRole("group", { name: /^Spend over time/ });
    // 24 h of 1-hour buckets, starting at the hour the window starts in.
    expect(within(chart).getAllByTestId("cost-column")).toHaveLength(25);
    expect(screen.getByText("1-hour buckets")).toBeInTheDocument();
    // Five non-zero segments across three buckets.
    const marks = chart.querySelectorAll("g[data-bucket] > *");
    expect(marks).toHaveLength(5);
    expect(
      [...marks].map((m) => m.getAttribute("fill")).filter((f) => f === "var(--chart-1)"),
    ).toHaveLength(3); // claude-sonnet-5 keeps slot 1
    // The peak bucket (5 h ago: $0.50 + $0.25) carries its total.
    expect(within(chart).getByText("$0.7500")).toBeInTheDocument();
  });

  it("shows every group's spend in a bucket on hover", async () => {
    mockCosts();
    const user = userEvent.setup();
    renderApp("/costs");
    const chart = await screen.findByRole("group", { name: /^Spend over time/ });
    const columns = within(chart).getAllByTestId("cost-column");
    // Columns start at 10:00 the day before; 05:00 today is column 19.
    await user.hover(columns[19]!);
    const tip = within(chart).getByRole("status");
    expect(tip).toHaveTextContent("Sep 25, 05:00 – 06:00");
    const rows = within(tip).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "$0.2500claude-haiku-4-5",
      "$0.5000claude-sonnet-5",
      "$0.7500Total",
    ]);
    await user.unhover(chart);
    expect(within(chart).queryByRole("status")).not.toBeInTheDocument();
  });

  it("reads buckets with the arrow keys once focused", async () => {
    mockCosts();
    const user = userEvent.setup();
    renderApp("/costs");
    const chart = await screen.findByRole("group", { name: /^Spend over time/ });
    act(() => {
      chart.focus();
    });
    // Focus starts on the newest bucket.
    expect(within(chart).getByRole("status")).toHaveTextContent("Sep 25, 10:00 – 11:00");
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(within(chart).getByRole("status")).toHaveTextContent("Sep 25, 08:00 – 09:00");
    expect(within(chart).getByRole("status")).toHaveTextContent("$0.7500claude-sonnet-5");
  });

  it("switches the grouping in the URL and refetches", async () => {
    const mock = mockCosts();
    const user = userEvent.setup();
    renderApp("/costs");
    await screen.findByRole("region", { name: "Totals for the range" });
    await user.click(
      within(screen.getByRole("group", { name: "Group by" })).getByRole("button", {
        name: "Client",
      }),
    );
    expect(getLocation()).toBe("/costs?group=client");
    await waitFor(() => {
      expect(
        within(legend())
          .getAllByRole("listitem")
          .map((li) => li.textContent),
      ).toEqual(["Claude Code$1.0083%", "SDK$0.200017%"]);
    });
    expect(costRequests(mock).at(-1)!.searchParams.get("group_by")).toBe("client");
    expect(tile("LLM calls")).toHaveTextContent("across 2 clients");
  });

  it("reads the range and grouping from the URL", async () => {
    const mock = mockCosts();
    const user = userEvent.setup();
    renderApp("/costs?range=7d&group=client");
    await screen.findByRole("region", { name: "Totals for the range" });
    const req = costRequests(mock)[0]!;
    expect(Date.parse(req.searchParams.get("from")!)).toBe(M3_NOW - 7 * 86_400_000);
    expect(req.searchParams.get("group_by")).toBe("client");
    await user.click(screen.getByRole("button", { name: "24h" }));
    expect(getLocation()).toBe("/costs?group=client");
  });

  it("shows the chart as a table, newest bucket first", async () => {
    mockCosts();
    const user = userEvent.setup();
    renderApp("/costs");
    await screen.findByRole("region", { name: "Totals for the range" });
    await user.click(screen.getByRole("button", { name: "Table" }));
    const table = screen.getByRole("table", { name: /Spend per 1-hour bucket/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Sep 25, 10:00 – 11:00");
    expect(rows[0]).toHaveTextContent("$0.2500–$0.0500$0.3000");
    expect(screen.queryByRole("group", { name: /^Spend over time/ })).not.toBeInTheDocument();
  });

  it("lists the most expensive traces, each linking to its trace", async () => {
    mockCosts();
    renderApp("/costs");
    await screen.findByText("Most expensive traces");
    const links = await screen.findAllByRole("link", { name: traceListPage1.traces[0]!.name });
    expect(links.map((a) => a.getAttribute("href"))).toContain(
      `/traces/${traceListPage1.traces[0]!.trace_id}`,
    );
  });

  it("lists the range's budget alerts: when, trace, scope, spent against the limit", async () => {
    const mock = mockCosts();
    const user = userEvent.setup();
    renderApp("/costs");
    await screen.findByRole("heading", { name: /^Budget alerts/ });
    await waitFor(() => {
      expect(alertRows()).toHaveLength(2);
    });
    const [run, session] = alertRows();
    expect(run).toHaveTextContent("Sep 25, 09:56:00");
    expect(run).toHaveTextContent("Run");
    expect(run).toHaveTextContent("$0.6200 / $0.5000");
    expect(within(run!).getByRole("link", { name: SUPPORT_TRACE_ID.slice(0, 8) })).toHaveAttribute(
      "href",
      `/traces/${SUPPORT_TRACE_ID}?span=0000000000000005`,
    );
    expect(session).toHaveTextContent("Session");
    expect(session).toHaveTextContent("$2.35 / $2.00");

    const req = alertRequests(mock)[0]!;
    expect(req.searchParams.getAll("kind")).toEqual(["budget"]);
    expect(Date.parse(req.searchParams.get("from")!)).toBe(M3_NOW - 86_400_000);

    await user.click(session!.querySelector("td")!);
    expect(getLocation()).toBe(`/traces/${SUPPORT_TRACE_ID}?span=0000000000000006`);
  });

  it("says so in one quiet line when no budget alerts fired", async () => {
    mockCosts({ alerts: () => noEvents });
    renderApp("/costs");
    expect(await screen.findByText("No budget alerts in the last 24 hours.")).toBeInTheDocument();
  });

  it("follows the range and the sample switch for budget alerts", async () => {
    setHideSamplePreference(true);
    const mock = mockCosts({ data: { sample_data: true, real_data: true } });
    renderApp("/costs?range=7d");
    await waitFor(() => {
      expect(alertRequests(mock).at(-1)!.searchParams.get("hide_sample")).toBe("true");
    });
    expect(Date.parse(alertRequests(mock).at(-1)!.searchParams.get("from")!)).toBe(
      M3_NOW - 7 * 86_400_000,
    );
  });

  it("leaves the budget alerts out on a server without guardrail events (501)", async () => {
    mockCosts({ alerts: () => new HttpError(501, { detail: "not implemented yet" }) });
    renderApp("/costs");
    await screen.findByText("Most expensive traces");
    await waitFor(() => {
      expect(screen.queryByText("Loading budget alerts")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: /^Budget alerts/ })).not.toBeInTheDocument();
  });

  it("shows the price table the costs come from", async () => {
    mockCosts();
    renderApp("/costs");
    expect(await screen.findByText(/list prices checked 2026-09-25/)).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "claude-sonnet-5" })).toBeInTheDocument();
  });

  it("offers a wider range when nothing was spent", async () => {
    const mock = mockCosts({ costs: () => emptyCosts });
    const user = userEvent.setup();
    renderApp("/costs");
    expect(await screen.findByText("No spend in the last 24 hours")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show last 30 days" }));
    expect(getLocation()).toBe("/costs?range=30d");
    await waitFor(() => {
      expect(Date.parse(costRequests(mock).at(-1)!.searchParams.get("from")!)).toBe(
        M3_NOW - 30 * 86_400_000,
      );
    });
  });

  it("explains a server without cost queries (501)", async () => {
    mockCosts({ costs: () => new HttpError(501, { detail: "cost series not implemented yet" }) });
    renderApp("/costs");
    expect(await screen.findByText("Cost queries aren't available yet")).toBeInTheDocument();
  });

  it("offers a retry when the costs fail to load", async () => {
    let fail = true;
    mockCosts({ costs: () => (fail ? new HttpError(500, { detail: "boom" }) : costsByModel) });
    const user = userEvent.setup();
    renderApp("/costs");
    expect(
      await screen.findByText("Couldn't load costs", {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "Totals for the range" })).toBeInTheDocument();
  });

  it("leaves sample data out when the sidebar switch says so", async () => {
    setHideSamplePreference(true);
    const mock = mockCosts({ data: { sample_data: true, real_data: true } });
    renderApp("/costs");
    await waitFor(() => {
      expect(costRequests(mock).at(-1)!.searchParams.get("hide_sample")).toBe("true");
      expect(traceRequests(mock).at(-1)!.searchParams.get("hide_sample")).toBe("true");
    });
  });
});
