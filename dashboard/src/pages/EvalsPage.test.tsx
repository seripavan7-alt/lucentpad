import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvalRun, EvalRunList } from "../api/types";
import { costDelta, EVAL_COMMAND } from "../features/evals/evals";
import { setHideSamplePreference } from "../lib/samplePreference";
import { BLOCKED_TRACE_ID, SUPPORT_TRACE_ID } from "../test/fixtures";
import { evalRun, M3_NOW, RUN_ID, runsPage1, runsPage2 } from "../test/m3Fixtures";
import { getLocation, HttpError, mockFetch, renderApp } from "../test/render";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(M3_NOW);
});

afterEach(() => {
  vi.useRealTimers();
  setHideSamplePreference(false);
});

function mockEvals({
  runs = (url: URL) => (url.searchParams.get("cursor") ? runsPage2 : runsPage1),
  run = () => evalRun,
  data = { sample_data: true, real_data: false },
}: {
  runs?: (url: URL) => EvalRunList | HttpError;
  run?: (url: URL) => EvalRun | HttpError;
  data?: object;
} = {}) {
  return mockFetch({
    "/v1/evals/runs": runs,
    "/v1/evals/runs/:id": run,
    "/v1/data": () => data,
  });
}

const runsRequests = (mock: ReturnType<typeof mockFetch>) =>
  mock.urls().filter((u) => u.pathname === "/v1/evals/runs");

const runRows = () => [...document.querySelectorAll<HTMLElement>("tr[data-run-id]")];
const caseGroup = (name: string) =>
  document.querySelector<HTMLElement>(`tbody[data-case="${name}"]`)!;

describe("Evals page: runs", () => {
  it("lists runs newest first with status, counts, regressions, cost, commit and CI link", async () => {
    mockEvals();
    renderApp("/evals");
    expect(screen.getByRole("heading", { level: 1, name: "Evals" })).toBeInTheDocument();
    await waitFor(() => {
      expect(runRows()).toHaveLength(2);
    });
    const [regressed, passed] = runRows();
    expect(regressed).toHaveTextContent("Regressed");
    expect(regressed).toHaveTextContent("support_agent");
    expect(regressed).toHaveTextContent("Sep 25, 09:55:00");
    expect(regressed).toHaveTextContent("2 / 4");
    expect(regressed).toHaveTextContent("$0.0125");
    expect(regressed).toHaveTextContent("prompt-tweak · 9f1c2d3");
    expect(within(regressed!).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      evalRun.ci_url,
    );
    expect(within(regressed!).getByRole("link", { name: "support_agent" })).toHaveAttribute(
      "href",
      `/evals/${RUN_ID}`,
    );
    expect(passed).toHaveTextContent("Passed");
    expect(passed).toHaveTextContent("6 / 6");
    expect(passed).toHaveTextContent("main · 0123456");
  });

  it("shows each run's cost against its baseline", async () => {
    const user = userEvent.setup();
    mockEvals();
    renderApp("/evals");
    await waitFor(() => {
      expect(runRows()).toHaveLength(2);
    });
    expect(screen.getByRole("columnheader", { name: "Cost vs baseline" })).toBeInTheDocument();
    const [regressed, passed] = runRows();
    const cost = (row: HTMLElement) => within(row).getAllByRole("cell")[5]!;
    expect(cost(regressed!)).toHaveTextContent("$0.0125+25%");
    expect(cost(regressed!)).toHaveAttribute("title", "baseline $0.0100");
    expect(cost(passed!)).toHaveTextContent("$0.0101−10%");
    // No cost: no change to show.
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(runRows()).toHaveLength(3);
    });
    expect(cost(runRows()[2]!)).toHaveTextContent(/^–$/);
  });

  it("asks without hide_sample by default, and with it when the sidebar switch says so", async () => {
    const shown = mockEvals();
    const { unmount } = renderApp("/evals");
    await waitFor(() => {
      expect(runRows()).toHaveLength(2);
    });
    expect(runsRequests(shown).every((u) => !u.searchParams.has("hide_sample"))).toBe(true);
    unmount();

    setHideSamplePreference(true);
    const hidden = mockEvals({ data: { sample_data: true, real_data: true } });
    renderApp("/evals");
    await waitFor(() => {
      expect(runsRequests(hidden).at(-1)!.searchParams.get("hide_sample")).toBe("true");
    });
  });

  it("loads more runs with the cursor", async () => {
    const mock = mockEvals();
    const user = userEvent.setup();
    renderApp("/evals");
    await waitFor(() => {
      expect(runRows()).toHaveLength(2);
    });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(runRows()).toHaveLength(3);
    });
    expect(runRows()[2]).toHaveTextContent("Error");
    const last = mock
      .urls()
      .filter((u) => u.pathname === "/v1/evals/runs")
      .at(-1)!;
    expect(last.searchParams.get("cursor")).toBe("r1.x");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("opens a run from its row", async () => {
    mockEvals();
    const user = userEvent.setup();
    renderApp("/evals");
    await waitFor(() => {
      expect(runRows()).toHaveLength(2);
    });
    await user.click(runRows()[0]!.querySelector("td")!);
    expect(getLocation()).toBe(`/evals/${RUN_ID}`);
    expect(
      await screen.findByRole("heading", { level: 1, name: /support_agent/ }),
    ).toBeInTheDocument();
  });

  it("explains how to record the first run", async () => {
    mockEvals({ runs: () => ({ runs: [], next_cursor: null }) });
    renderApp("/evals");
    expect(await screen.findByText("No eval runs yet")).toBeInTheDocument();
    expect(screen.getByText(EVAL_COMMAND)).toBeInTheDocument();
  });

  it("explains a server without eval runs (501)", async () => {
    mockEvals({ runs: () => new HttpError(501, { detail: "eval runs not implemented yet" }) });
    renderApp("/evals");
    expect(await screen.findByText("Eval runs aren't available yet")).toBeInTheDocument();
  });
});

describe("costDelta", () => {
  it("signs the change against the baseline, or null without one", () => {
    expect(costDelta(0.0125, 0.01)).toBe("+25%");
    expect(costDelta(0.009, 0.01)).toBe("−10%");
    expect(costDelta(0.01, 0.01)).toBe("±0%");
    expect(costDelta(0.01, null)).toBeNull();
    expect(costDelta(null, 0.01)).toBeNull();
    expect(costDelta(0.01, 0)).toBeNull();
  });
});

describe("Evals page: run detail", () => {
  it("shows the run's status, commit, CI link and totals against the baseline", async () => {
    mockEvals();
    renderApp(`/evals/${RUN_ID}`);
    const heading = await screen.findByRole("heading", { level: 1, name: /support_agent/ });
    expect(within(heading).getByRole("link", { name: "Evals" })).toHaveAttribute("href", "/evals");
    expect(screen.getAllByText("Regressed").length).toBeGreaterThan(0);
    expect(screen.getByText("prompt-tweak", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("9f1c2d3")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CI run" })).toHaveAttribute("href", evalRun.ci_url);
    expect(screen.getByText("claude-haiku-4-5")).toBeInTheDocument();

    const tiles = screen.getByRole("region", { name: "Run summary" });
    expect(within(tiles).getByRole("group", { name: "Cases passed" })).toHaveTextContent("2 of 4");
    expect(within(tiles).getByRole("group", { name: "Cases passed" })).toHaveTextContent(
      "2 failed · 1 new",
    );
    expect(within(tiles).getByRole("group", { name: "Regressions" })).toHaveTextContent("1");
    expect(within(tiles).getByRole("group", { name: "Cost" })).toHaveTextContent(
      "$0.0125+25% vs baseline $0.0100",
    );
  });

  it("lists cases regressions first and flags regressions, fixes and new cases", async () => {
    mockEvals();
    renderApp(`/evals/${RUN_ID}`);
    await screen.findByRole("heading", { level: 1, name: /support_agent/ });
    const order = [...document.querySelectorAll<HTMLElement>("tbody[data-case]")].map(
      (g) => g.dataset.case,
    );
    expect(order).toEqual(["refund_over_limit", "reschedule", "order_status", "small_talk"]);

    const regressed = caseGroup("refund_over_limit");
    expect(regressed).toHaveAttribute("data-change", "regressed");
    expect(regressed).toHaveTextContent("Fail");
    expect(within(regressed).getByText("Regression")).toBeInTheDocument();
    expect(regressed).toHaveTextContent("1 / 2");
    expect(regressed).toHaveTextContent("3.40s");
    expect(regressed).toHaveTextContent("$0.0040");
    expect(regressed).toHaveTextContent("I've issued the refund.");
    // Failed checks are spelled out with their detail; passing ones aren't.
    const failed = within(regressed).getByRole("list", {
      name: "Failed checks for refund_over_limit",
    });
    expect(failed).toHaveTextContent("contains: colleague");
    expect(failed).toHaveTextContent("output doesn't mention a colleague");
    expect(failed).not.toHaveTextContent("issue_refund");
    expect(within(regressed).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      `/traces/${BLOCKED_TRACE_ID}`,
    );

    expect(within(caseGroup("small_talk")).getByText("Fixed")).toBeInTheDocument();
    expect(caseGroup("reschedule")).toHaveTextContent("New case");
    expect(caseGroup("order_status")).toHaveTextContent("Pass");
    expect(within(caseGroup("order_status")).queryByRole("list")).not.toBeInTheDocument();
    expect(within(caseGroup("order_status")).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      `/traces/${SUPPORT_TRACE_ID}`,
    );
  });

  it("says when a run doesn't exist", async () => {
    mockEvals({ run: () => new HttpError(404, { detail: "eval run not found" }) });
    renderApp("/evals/nope");
    expect(await screen.findByText("This eval run doesn't exist")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All runs" })).toHaveAttribute("href", "/evals");
  });
});
