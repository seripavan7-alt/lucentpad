import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BASE_TIME,
  SUPPORT_TRACE_ID,
  supportDetail,
  traceListPage1,
  traceListPage2,
} from "../test/fixtures";
import { getLocation, HttpError, mockFetch, renderApp } from "../test/render";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(BASE_TIME + 10 * 60_000);
});

afterEach(() => {
  vi.useRealTimers();
});

const traceRequests = (mock: ReturnType<typeof mockFetch>) =>
  mock.urls().filter((u) => u.pathname === "/v1/traces");

describe("Traces list", () => {
  it("renders a row per trace with formatted cost, tokens and duration", async () => {
    mockFetch({ "/v1/traces": () => traceListPage1 });
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
    expect(support.getByText("10m ago")).toBeInTheDocument();
    expect(support.getByText("OK")).toBeInTheDocument();

    const claude = within(rows[2]!);
    expect(claude.getByText("Claude Code")).toBeInTheDocument();
    expect(claude.getByText("1m 35s")).toBeInTheDocument();
    expect(claude.getByText("$1.01")).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("100k / 2.4k");
    expect(claude.getByText("+1")).toBeInTheDocument(); // second model

    expect(within(rows[1]!).getByText("Blocked")).toBeInTheDocument();
  });

  it("reflects filters in the URL and the API query", async () => {
    const mock = mockFetch({ "/v1/traces": () => traceListPage1 });
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");

    const source = screen.getByRole("group", { name: "Source" });
    await user.click(within(source).getByRole("button", { name: "Gateway" }));
    expect(getLocation()).toBe("/traces?source=gateway");

    const status = screen.getByRole("group", { name: "Status" });
    await user.click(within(status).getByRole("button", { name: "Error" }));
    expect(getLocation()).toBe("/traces?source=gateway&status=error");
    expect(within(status).getByRole("button", { name: "Error" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await waitFor(() => {
      const last = traceRequests(mock).at(-1)!;
      expect(last.searchParams.get("source")).toBe("gateway");
      expect(last.searchParams.get("status")).toBe("error");
    });

    await user.click(within(source).getByRole("button", { name: "All" }));
    expect(getLocation()).toBe("/traces?status=error");
  });

  it("reads initial filters from the URL and ignores invalid values", async () => {
    const mock = mockFetch({ "/v1/traces": () => traceListPage1 });
    renderApp("/traces?source=sdk&status=bogus");
    await screen.findByRole("table");
    const url = traceRequests(mock)[0]!;
    expect(url.searchParams.get("source")).toBe("sdk");
    expect(url.searchParams.has("status")).toBe(false);
  });

  it("loads more pages with next_cursor", async () => {
    const mock = mockFetch({
      "/v1/traces": (url) => (url.searchParams.get("cursor") ? traceListPage2 : traceListPage1),
    });
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("copilot-cli request")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(5);
    expect(traceRequests(mock).at(-1)!.searchParams.get("cursor")).toBe("cursor-page-2");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("keeps loaded rows when a later page fails", async () => {
    mockFetch({
      "/v1/traces": (url) =>
        url.searchParams.get("cursor") ? new HttpError(500, { detail: "boom" }) : traceListPage1,
    });
    const user = userEvent.setup();
    renderApp("/traces");
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load more traces: boom");
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("shows an error state with retry", async () => {
    let fail = true;
    mockFetch({
      "/v1/traces": () =>
        fail ? new HttpError(500, { detail: "database unavailable" }) : traceListPage1,
    });
    const user = userEvent.setup();
    renderApp("/traces");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load traces");
    expect(alert).toHaveTextContent("database unavailable");

    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("shows empty states", async () => {
    mockFetch({ "/v1/traces": () => ({ traces: [], next_cursor: null }) });
    const user = userEvent.setup();
    renderApp("/traces");
    expect(await screen.findByText("No traces yet")).toBeInTheDocument();

    const status = screen.getByRole("group", { name: "Status" });
    await user.click(within(status).getByRole("button", { name: "Blocked" }));
    expect(await screen.findByText("No traces match these filters")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(getLocation()).toBe("/traces");
  });

  it("navigates to the detail page on row click", async () => {
    mockFetch({
      "/v1/traces": () => traceListPage1,
      "/v1/traces/:id": () => supportDetail,
    });
    const user = userEvent.setup();
    renderApp("/traces");
    const table = await screen.findByRole("table");
    await user.click(within(table).getAllByText("4.20s")[0]!);
    expect(getLocation()).toBe(`/traces/${SUPPORT_TRACE_ID}`);
    expect(await screen.findByRole("list", { name: "Spans" })).toBeInTheDocument();
  });
});
