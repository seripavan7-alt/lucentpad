import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TraceSummary } from "../api/types";
import {
  BASE_TIME,
  SUPPORT_TRACE_ID,
  supportDetail,
  supportSummary,
  traceFacets,
  traceListPage1,
  traceListPage2,
} from "../test/fixtures";
import { getLocation, HttpError, mockFetch, renderApp } from "../test/render";

const NOW = BASE_TIME + 10 * 60_000;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

type Mock = ReturnType<typeof mockFetch>;

/** Page requests (not live polls, which carry `since`). */
const listRequests = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/traces" && !u.searchParams.has("since"));
const livePolls = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/traces" && u.searchParams.has("since"));
const facetRequests = (mock: Mock) => mock.urls().filter((u) => u.pathname === "/v1/traces/facets");

function mockList(list: (url: URL) => unknown = () => traceListPage1) {
  return mockFetch({
    "/v1/traces": list,
    "/v1/traces/facets": () => traceFacets,
    "/v1/traces/:id": () => supportDetail,
  });
}

const fromOf = (url: URL) => Date.parse(url.searchParams.get("from") ?? "");
const dataRows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
const panel = () => screen.getByRole("complementary", { name: "Filters" });
const group = (name: string) => within(panel()).getByRole("region", { name });
const facetsLoaded = () =>
  within(screen.getByRole("region", { name: "Status" })).findByRole("checkbox", { name: "OK" });
const table = () => within(screen.getByRole("table"));

describe("Traces list", () => {
  it("renders a row per trace with formatted cost, tokens, duration and start time", async () => {
    mockList();
    renderApp("/traces");

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);

    const support = within(rows[0]!);
    expect(support.getByRole("link", { name: "support-agent" })).toHaveAttribute(
      "href",
      `/traces/${SUPPORT_TRACE_ID}`,
    );
    expect(support.getByText("4.20s")).toBeInTheDocument();
    expect(support.getByText("$0.0209")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("4.1k / 560");
    expect(support.getByText("OK")).toBeInTheDocument();

    const claude = within(rows[2]!);
    expect(claude.getByText("Claude Code")).toBeInTheDocument();
    expect(claude.getByText("1m 35s")).toBeInTheDocument();
    expect(claude.getByText("$1.01")).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("100k / 2.4k");
    expect(claude.getByText("+1")).toBeInTheDocument(); // second model

    expect(within(rows[1]!).getByText("Blocked")).toBeInTheDocument();
  });

  it("shows the absolute local start time, with relative time and ISO UTC on hover", async () => {
    mockList();
    renderApp("/traces");
    await screen.findByRole("table");
    const time = within(dataRows()[0]!).getByText("Sep 25, 10:00:00"); // TZ=UTC in tests
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("dateTime", supportSummary.start_time);
    const cell = time.closest("td")!;
    expect(cell).toHaveAttribute("title", "10m ago · 2026-09-25T10:00:00Z");
    expect(cell).toHaveClass("num");
    expect(within(dataRows()[2]!).getByText("Sep 25, 08:00:00")).toBeInTheDocument();
  });

  it("shows a one-line Input → Output preview, full text on hover, – when not captured", async () => {
    mockList();
    renderApp("/traces");
    await screen.findByRole("table");
    expect(screen.getByRole("columnheader", { name: "Input → Output" })).toBeInTheDocument();
    const [support, blocked] = screen.getAllByTestId("preview");
    expect(support).toHaveTextContent(
      "Where's order 1042? I want a refund. → I've issued a refund of $42.00 for order 1042.",
    );
    expect(support).toHaveAttribute(
      "title",
      "Input: Where's order 1042?\nI want a refund.\n\nOutput: I've issued a refund of $42.00 for order 1042.",
    );
    expect(blocked).toHaveTextContent(/^–$/);
    expect(blocked).not.toHaveAttribute("title");
  });

  it("asks for the last 15 minutes, newest first, by default", async () => {
    const mock = mockList();
    renderApp("/traces");
    await screen.findByRole("table");
    const first = listRequests(mock)[0]!;
    expect(fromOf(first)).toBe(NOW - 15 * 60_000);
    expect(first.searchParams.get("order")).toBe("desc");
    expect(first.searchParams.get("limit")).toBe("50");
    expect(first.searchParams.has("to")).toBe(false);
    expect(fromOf(facetRequests(mock)[0]!)).toBe(NOW - 15 * 60_000);
    const range = screen.getByRole("group", { name: "Time range" });
    expect(within(range).getByRole("button", { name: "15m" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // The old top-bar segmented filters are gone.
    expect(screen.queryByRole("group", { name: "Source" })).not.toBeInTheDocument();
  });

  it("switches the range: URL, list and facets refetch with a new window", async () => {
    const mock = mockList();
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");

    const range = screen.getByRole("group", { name: "Time range" });
    await user.click(within(range).getByRole("button", { name: "1h" }));
    expect(getLocation()).toBe("/traces?range=1h");
    await waitFor(() => {
      expect(fromOf(listRequests(mock).at(-1)!)).toBe(NOW - 3600_000);
    });
    await waitFor(() => {
      expect(fromOf(facetRequests(mock).at(-1)!)).toBe(NOW - 3600_000);
    });

    await user.click(within(range).getByRole("button", { name: "15m" }));
    expect(getLocation()).toBe("/traces");
  });

  it("recomputes the rolling window on refresh", async () => {
    const mock = mockList();
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");
    const before = listRequests(mock).length;

    vi.setSystemTime(NOW + 60_000);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      expect(listRequests(mock).length).toBeGreaterThan(before);
    });
    expect(fromOf(listRequests(mock).at(-1)!)).toBe(NOW + 60_000 - 15 * 60_000);
    await waitFor(() => {
      expect(fromOf(facetRequests(mock).at(-1)!)).toBe(NOW + 60_000 - 15 * 60_000);
    });
  });

  it("renders facet groups with counts sorted by count", async () => {
    mockList();
    renderApp("/traces");
    await screen.findByRole("table");
    await facetsLoaded();

    for (const name of ["Name", "Status", "Source", "Client", "Model", "Service"]) {
      expect(group(name)).toBeInTheDocument();
    }
    const status = group("Status");
    const labels = within(status)
      .getAllByRole("checkbox")
      .map((c) => c.getAttribute("aria-label"));
    expect(labels).toEqual(["OK", "Error", "Blocked"]);
    expect(
      within(status)
        .getAllByTestId("facet-count")
        .map((c) => c.textContent),
    ).toEqual(["15", "3", "1"]);
    expect(within(group("Client")).getByRole("checkbox", { name: "Claude Code" })).toBeVisible();
  });

  it("ticks values into repeated URL params and query params", async () => {
    const mock = mockList();
    const user = userEvent.setup();
    renderApp("/traces");
    await facetsLoaded();

    await user.click(within(group("Name")).getByRole("checkbox", { name: "support-agent" }));
    await user.click(within(group("Name")).getByRole("checkbox", { name: "claude-code session" }));
    await user.click(within(group("Status")).getByRole("checkbox", { name: "Error" }));

    const url = new URL(getLocation(), "http://x");
    expect(url.searchParams.getAll("name")).toEqual(["support-agent", "claude-code session"]);
    expect(url.searchParams.getAll("status")).toEqual(["error"]);
    expect(within(group("Status")).getByRole("checkbox", { name: "Error" })).toBeChecked();

    await waitFor(() => {
      const last = listRequests(mock).at(-1)!;
      expect(last.searchParams.getAll("name")).toEqual(["claude-code session", "support-agent"]);
      expect(last.searchParams.getAll("status")).toEqual(["error"]);
    });
    await waitFor(() => {
      expect(facetRequests(mock).at(-1)!.searchParams.getAll("status")).toEqual(["error"]);
    });

    // Unticking removes just that value.
    await user.click(within(group("Name")).getByRole("checkbox", { name: "support-agent" }));
    expect(new URL(getLocation(), "http://x").searchParams.getAll("name")).toEqual([
      "claude-code session",
    ]);
  });

  it("searches Name, and any facet with more than 10 values", async () => {
    mockList();
    const user = userEvent.setup();
    renderApp("/traces");
    await facetsLoaded();

    const name = group("Name");
    await user.type(within(name).getByRole("searchbox", { name: "Search Name" }), "claude");
    expect(
      within(name)
        .getAllByRole("checkbox")
        .map((c) => c.getAttribute("aria-label")),
    ).toEqual(["claude-code session"]);
    await user.clear(within(name).getByRole("searchbox", { name: "Search Name" }));
    expect(within(name).getAllByRole("checkbox")).toHaveLength(3);

    // Model has 12 values → searchable; Status has 3 → not.
    const model = group("Model");
    await user.type(within(model).getByRole("searchbox", { name: "Search Model" }), "model-1");
    expect(
      within(model)
        .getAllByRole("checkbox")
        .map((c) => c.getAttribute("aria-label")),
    ).toEqual(["model-1", "model-10", "model-11", "model-12"]);
    expect(within(group("Status")).queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("clears one group, or everything with Clear all (showing the active count)", async () => {
    mockList();
    const user = userEvent.setup();
    renderApp("/traces?status=error&status=blocked&source=sdk&range=4h");
    await facetsLoaded();

    await user.click(within(panel()).getByRole("button", { name: "Clear Status" }));
    expect(getLocation()).toBe("/traces?source=sdk&range=4h");

    await user.click(within(group("Name")).getByRole("checkbox", { name: "support-agent" }));
    const clearAll = within(panel()).getByRole("button", { name: "Clear all (2)" });
    await user.click(clearAll);
    expect(getLocation()).toBe("/traces?range=4h");
    expect(within(panel()).queryByRole("button", { name: /Clear all/ })).not.toBeInTheDocument();
  });

  it("keeps selected values visible at count 0", async () => {
    mockList();
    renderApp("/traces?model=gpt-9&client=copilot-chat");
    const box = await within(
      await screen.findByRole("complementary", { name: "Filters" }),
    ).findByRole("checkbox", { name: "gpt-9" });
    expect(box).toBeChecked();
    expect(box.closest("label")).toHaveTextContent("gpt-90");
    const copilot = within(group("Client")).getByRole("checkbox", { name: "Copilot Chat" });
    expect(copilot).toBeChecked();
    const labels = within(group("Client"))
      .getAllByRole("checkbox")
      .map((c) => c.getAttribute("aria-label"));
    expect(labels.at(-1)).toBe("Copilot Chat"); // count 0 sorts last
  });

  it("restores the whole view from the URL", async () => {
    const mock = mockList();
    renderApp("/traces?range=7d&order=asc&name=support-agent&status=error&status=bogus&source=sdk");
    await screen.findByRole("table");
    const first = listRequests(mock)[0]!;
    expect(fromOf(first)).toBe(NOW - 7 * 86_400_000);
    expect(first.searchParams.get("order")).toBe("asc");
    expect(first.searchParams.getAll("name")).toEqual(["support-agent"]);
    expect(first.searchParams.getAll("status")).toEqual(["error"]);
    expect(first.searchParams.getAll("source")).toEqual(["sdk"]);
    const range = screen.getByRole("group", { name: "Time range" });
    expect(within(range).getByRole("button", { name: "7d" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      await within(group("Name")).findByRole("checkbox", { name: "support-agent" }),
    ).toBeChecked();
    expect(within(group("Source")).getByRole("checkbox", { name: "SDK" })).toBeChecked();
    expect(screen.getByRole("columnheader", { name: /Started/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("loads more pages with next_cursor and the same window", async () => {
    const mock = mockList((url) =>
      url.searchParams.get("cursor") ? traceListPage2 : traceListPage1,
    );
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");

    vi.setSystemTime(NOW + 5000);
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(
      await within(await screen.findByRole("table")).findByText("copilot-cli request"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(5);
    const [page1, page2] = listRequests(mock);
    expect(page2!.searchParams.get("cursor")).toBe("cursor-page-2");
    // The cursor is bound to the filter set, so page 2 must reuse page 1's window.
    expect(page2!.searchParams.get("from")).toBe(page1!.searchParams.get("from"));
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("resets paging when a filter changes", async () => {
    const mock = mockList((url) =>
      url.searchParams.get("cursor") ? traceListPage2 : traceListPage1,
    );
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(
      await within(await screen.findByRole("table")).findByText("copilot-cli request"),
    ).toBeInTheDocument();

    await user.click(await within(group("Status")).findByRole("checkbox", { name: "OK" }));
    await waitFor(() => {
      expect(table().queryByText("copilot-cli request")).not.toBeInTheDocument();
    });
    const last = listRequests(mock).at(-1)!;
    expect(last.searchParams.has("cursor")).toBe(false);
    expect(last.searchParams.getAll("status")).toEqual(["ok"]);
    expect(dataRows()).toHaveLength(3);
  });

  it("sorts by Started: toggles order, URL, aria-sort, and resets paging", async () => {
    const mock = mockList((url) =>
      url.searchParams.get("cursor") ? traceListPage2 : traceListPage1,
    );
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");
    const header = screen.getByRole("columnheader", { name: /Started/ });
    expect(header).toHaveAttribute("aria-sort", "descending");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(
      await within(await screen.findByRole("table")).findByText("copilot-cli request"),
    ).toBeInTheDocument();

    await user.click(within(header).getByRole("button", { name: /Started/ }));
    expect(getLocation()).toBe("/traces?order=asc");
    await waitFor(() => {
      expect(listRequests(mock).at(-1)!.searchParams.get("order")).toBe("asc");
    });
    expect(listRequests(mock).at(-1)!.searchParams.has("cursor")).toBe(false);
    await waitFor(() => {
      expect(screen.getByRole("columnheader", { name: /Started/ })).toHaveAttribute(
        "aria-sort",
        "ascending",
      );
    });
    await waitFor(() => {
      expect(dataRows()).toHaveLength(3);
    });

    await user.click(screen.getByRole("button", { name: /Started/ }));
    expect(getLocation()).toBe("/traces");
  });

  it("keeps loaded rows when a later page fails", async () => {
    mockList((url) =>
      url.searchParams.get("cursor") ? new HttpError(500, { detail: "boom" }) : traceListPage1,
    );
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load more traces: boom");
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("shows an error state with retry", async () => {
    let fail = true;
    mockList(() =>
      fail ? new HttpError(500, { detail: "database unavailable" }) : traceListPage1,
    );
    const user = userEvent.setup();
    renderApp("/traces");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load traces");
    expect(alert).toHaveTextContent("database unavailable");

    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("shows a range-aware empty state that widens to 24 hours", async () => {
    const mock = mockList(() => ({ traces: [], next_cursor: null, as_of: "2026-09-25T10:10:00Z" }));
    const user = userEvent.setup();
    renderApp("/traces");
    expect(await screen.findByText("No traces in the last 15 minutes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show last 24 hours" }));
    expect(getLocation()).toBe("/traces?range=24h");
    expect(await screen.findByText("No traces in the last 24 hours")).toBeInTheDocument();
    await waitFor(() => {
      expect(fromOf(listRequests(mock).at(-1)!)).toBe(NOW - 86_400_000);
    });
    expect(screen.getByRole("button", { name: "Show last 30 days" })).toBeInTheDocument();
  });

  it("offers Clear filters in the empty state when filters are active", async () => {
    mockList(() => ({ traces: [], next_cursor: null, as_of: "2026-09-25T10:10:00Z" }));
    const user = userEvent.setup();
    renderApp("/traces?range=1h&status=blocked");
    expect(
      await screen.findByText("No traces match these filters in the last hour"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(getLocation()).toBe("/traces?range=1h");
  });

  it("collapses the filter panel behind a Filters button on narrow screens", async () => {
    mockList();
    const user = userEvent.setup();
    renderApp("/traces?status=error");
    await screen.findByRole("table");
    const button = screen.getByRole("button", { name: /^Filters/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveTextContent("1"); // active filter count
    expect(panel()).toHaveAttribute("data-open", "false");
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panel()).toHaveAttribute("data-open", "true");
  });

  it("navigates to the detail page on row click", async () => {
    mockList();
    const user = userEvent.setup();
    renderApp("/traces");
    const table = await screen.findByRole("table");
    await user.click(within(table).getAllByText("4.20s")[0]!);
    expect(getLocation()).toBe(`/traces/${SUPPORT_TRACE_ID}`);
    expect(await screen.findByRole("list", { name: "Spans" })).toBeInTheDocument();
  });
});

describe("Traces list, live", () => {
  const AS_OF_1 = "2026-09-25T10:10:00Z";
  const AS_OF_2 = "2026-09-25T10:10:03Z";

  const newTrace: TraceSummary = {
    ...supportSummary,
    trace_id: "1f00000000000000000000000000abcd",
    name: "support-agent-new",
    start_time: "2026-09-25T10:10:01.000Z",
    input_preview: null,
    output_preview: null,
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
  });

  function liveMock(poll: (url: URL) => unknown) {
    return mockList((url) => {
      if (url.searchParams.has("since")) return poll(url);
      return url.searchParams.get("cursor")
        ? traceListPage2
        : { ...traceListPage1, as_of: AS_OF_1 };
    });
  }

  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  it("polls every 3 s with since, prepends new traces and keeps loaded pages", async () => {
    const updatedSupport = { ...supportSummary, duration_ms: 5200 };
    const mock = liveMock(() => ({
      traces: [newTrace, updatedSupport],
      next_cursor: null,
      as_of: AS_OF_2,
    }));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderApp("/traces?status=ok");
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(
      await within(await screen.findByRole("table")).findByText("copilot-cli request"),
    ).toBeInTheDocument();
    expect(livePolls(mock)).toHaveLength(0);

    await tick(3000);
    await waitFor(() => {
      expect(dataRows()).toHaveLength(5);
    });
    const poll = livePolls(mock)[0]!;
    expect(poll.searchParams.get("since")).toBe(AS_OF_1);
    expect(poll.searchParams.getAll("status")).toEqual(["ok"]);
    expect(poll.searchParams.get("order")).toBe("desc");
    expect(poll.searchParams.has("cursor")).toBe(false);

    const rows = dataRows();
    expect(rows[0]).toHaveTextContent("support-agent-new");
    expect(rows[0]).toHaveAttribute("data-fresh", "true");
    expect(rows[1]).not.toHaveAttribute("data-fresh");
    expect(rows[1]).toHaveTextContent("5.20s"); // updated in place
    expect(table().getByText("copilot-cli request")).toBeInTheDocument(); // page 2 kept
    expect(getLocation()).toBe("/traces?status=ok");

    // The next poll passes the new as_of; the highlight fades.
    await tick(3000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(2);
    });
    expect(livePolls(mock)[1]!.searchParams.get("since")).toBe(AS_OF_2);
    expect(dataRows()).toHaveLength(5); // not duplicated
    await waitFor(() => {
      expect(dataRows()[0]).not.toHaveAttribute("data-fresh");
    });
  });

  it("does not prepend when sorted oldest first", async () => {
    const mock = liveMock(() => ({ traces: [newTrace], next_cursor: null, as_of: AS_OF_2 }));
    renderApp("/traces?order=asc");
    await screen.findByRole("table");
    await tick(3000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(1);
    });
    expect(livePolls(mock)[0]!.searchParams.get("order")).toBe("asc");
    expect(screen.queryByText("support-agent-new")).not.toBeInTheDocument();
    expect(dataRows()).toHaveLength(3);
  });

  it("stops polling in hidden tabs and resumes when visible", async () => {
    let state: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
    const mock = liveMock(() => ({ traces: [], next_cursor: null, as_of: AS_OF_2 }));
    renderApp("/traces");
    await screen.findByRole("table");

    state = "hidden";
    await tick(10_000);
    expect(livePolls(mock)).toHaveLength(0);

    state = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(1);
    });
  });

  it("backs off on errors", async () => {
    let fail = true;
    const mock = liveMock(() =>
      fail
        ? new HttpError(503, { detail: "down" })
        : { traces: [], next_cursor: null, as_of: AS_OF_2 },
    );
    renderApp("/traces");
    await screen.findByRole("table");

    await tick(3000); // poll 1 fails → wait 6 s
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(1);
    });
    await tick(5000);
    expect(livePolls(mock)).toHaveLength(1);
    await tick(1000); // poll 2 fails → wait 12 s
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(2);
    });
    fail = false;
    await tick(11_000);
    expect(livePolls(mock)).toHaveLength(2);
    await tick(1000); // poll 3 succeeds → back to 3 s
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(3);
    });
    await tick(3000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(4);
    });
    // The page itself stays up.
    expect(dataRows()).toHaveLength(3);
  });
});
