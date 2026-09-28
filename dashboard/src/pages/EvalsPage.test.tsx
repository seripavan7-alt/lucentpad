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

const runItems = () => [...document.querySelectorAll<HTMLElement>("a[data-run-id]")];
const runItem = (id: string) => document.querySelector<HTMLElement>(`a[data-run-id="${id}"]`)!;
const caseCard = (name: string) => document.querySelector<HTMLElement>(`li[data-case="${name}"]`)!;
const panel = () => screen.getByRole("region", { name: "Selected eval run" });
const detailRequests = (mock: ReturnType<typeof mockFetch>) =>
  mock.urls().filter((u) => u.pathname.startsWith("/v1/evals/runs/"));

describe("Evals page: runs list", () => {
  it("lists runs newest first with status, date, commit, pass count, regressions and cost", async () => {
    mockEvals();
    renderApp("/evals");
    expect(screen.getByRole("heading", { level: 1, name: "Evals" })).toBeInTheDocument();
    await waitFor(() => {
      expect(runItems()).toHaveLength(2);
    });
    const regressed = runItem(RUN_ID);
    expect(regressed).toHaveAttribute("data-status", "regressed");
    expect(regressed).toHaveTextContent("Regressed");
    expect(regressed).toHaveTextContent("Sep 25, 09:55:00");
    expect(regressed).toHaveTextContent("support_agent");
    expect(regressed).toHaveTextContent("prompt-tweak · 9f1c2d3");
    expect(regressed).toHaveTextContent("2/4 passed");
    expect(regressed).toHaveTextContent("1 regression");
    expect(regressed).toHaveTextContent("$0.0125 +25%");
    expect(regressed).toHaveAttribute("href", `/evals/${RUN_ID}`);
    // One square per case: the regression, the other failure, then the passes.
    const dots = [...regressed.querySelectorAll("i[data-kind]")].map(
      (d) => (d as HTMLElement).dataset.kind,
    );
    expect(dots).toEqual(["regressed", "failed", "passed", "passed"]);

    const passed = runItem("run-b");
    expect(passed).toHaveTextContent("Passed");
    expect(passed).toHaveTextContent("6/6 passed");
    expect(passed).toHaveTextContent("main · 0123456");
    expect(passed).toHaveTextContent("$0.0101 −10%");
    expect(passed).not.toHaveTextContent("regression");
  });

  it("selects the newest run on /evals and marks it as the selected run", async () => {
    const mock = mockEvals();
    renderApp("/evals");
    await waitFor(() => {
      expect(runItems()).toHaveLength(2);
    });
    expect(runItem(RUN_ID)).toHaveAttribute("aria-current", "true");
    expect(runItem("run-b")).not.toHaveAttribute("aria-current");
    expect(await within(panel()).findByText("Selected run")).toBeInTheDocument();
    expect(panel()).toHaveTextContent("1 of 2+ · newest");
    expect(detailRequests(mock).at(-1)!.pathname).toBe(`/v1/evals/runs/${RUN_ID}`);
  });

  it("opens a run from the list: the URL, the highlight and the panel all follow", async () => {
    const user = userEvent.setup();
    const mock = mockEvals();
    renderApp("/evals");
    await waitFor(() => {
      expect(runItems()).toHaveLength(2);
    });
    await user.click(runItem("run-b"));
    expect(getLocation()).toBe("/evals/run-b");
    expect(runItem("run-b")).toHaveAttribute("aria-current", "true");
    expect(runItem(RUN_ID)).not.toHaveAttribute("aria-current");
    await waitFor(() => {
      expect(detailRequests(mock).at(-1)!.pathname).toBe("/v1/evals/runs/run-b");
    });
    expect(await within(panel()).findByText(/2 of 2\+/)).toBeInTheDocument();
  });

  it("asks without hide_sample by default, and with it when the sidebar switch says so", async () => {
    const shown = mockEvals();
    const { unmount } = renderApp("/evals");
    await waitFor(() => {
      expect(runItems()).toHaveLength(2);
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
    const user = userEvent.setup();
    const mock = mockEvals();
    renderApp("/evals");
    await waitFor(() => {
      expect(runItems()).toHaveLength(2);
    });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(runItems()).toHaveLength(3);
    });
    expect(runItems()[2]).toHaveTextContent("Error");
    expect(runItems()[2]).toHaveTextContent("–");
    const last = runsRequests(mock).at(-1)!;
    expect(last.searchParams.get("cursor")).toBe("r1.x");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("explains how to record the first run", async () => {
    mockEvals({ runs: () => ({ runs: [], next_cursor: null }) });
    renderApp("/evals");
    expect(await screen.findByText("No eval runs yet")).toBeInTheDocument();
    expect(screen.getByText(EVAL_COMMAND)).toBeInTheDocument();
  });

  it("explains a server without eval runs (501)", async () => {
    mockEvals({ runs: () => new HttpError(501, { detail: "not implemented" }) });
    renderApp("/evals");
    expect(await screen.findByText("Eval runs aren't available yet")).toBeInTheDocument();
  });

  it("signs the change against the baseline, or null without one", () => {
    expect(costDelta(0.0125, 0.01)).toBe("+25%");
    expect(costDelta(0.009, 0.01)).toBe("−10%");
    expect(costDelta(0.01, 0.01)).toBe("±0%");
    expect(costDelta(0.01, null)).toBeNull();
    expect(costDelta(null, 0.01)).toBeNull();
    expect(costDelta(0.01, 0)).toBeNull();
  });
});

describe("Evals page: the selected run", () => {
  it("heads the panel with the run's suite, time, status, commit, model and CI link", async () => {
    mockEvals();
    renderApp(`/evals/${RUN_ID}`);
    await screen.findByRole("region", { name: "Selected eval run" });
    const title = await within(panel()).findByRole("heading", { level: 2, name: /support_agent/ });
    expect(title).toHaveTextContent("Sep 25, 09:55:00");
    expect(within(panel()).getByText("Regressed")).toBeInTheDocument();
    expect(panel()).toHaveTextContent("prompt-tweak · 9f1c2d3");
    expect(within(panel()).getByText("claude-haiku-4-5")).toBeInTheDocument();
    expect(within(panel()).getByText("18.4s")).toBeInTheDocument();
    expect(within(panel()).getByRole("link", { name: /CI run/ })).toHaveAttribute(
      "href",
      evalRun.ci_url,
    );
    expect(screen.getByRole("link", { name: "← All runs" })).toHaveAttribute("href", "/evals");

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

  it("lists this run's cases inside the panel, regressions first, with change tags", async () => {
    mockEvals();
    renderApp(`/evals/${RUN_ID}`);
    await screen.findByRole("region", { name: "Selected eval run" });
    const list = await within(panel()).findByRole("list", {
      name: "Cases in the support_agent run of Sep 25, 09:55:00",
    });
    expect(within(panel()).getByRole("heading", { name: /Cases in this run/ })).toHaveTextContent(
      "4",
    );
    const order = [...list.querySelectorAll<HTMLElement>("li[data-case]")].map(
      (c) => c.dataset.case,
    );
    expect(order).toEqual(["refund_over_limit", "reschedule", "order_status", "small_talk"]);

    const regressed = caseCard("refund_over_limit");
    expect(regressed).toHaveAttribute("data-change", "regressed");
    expect(regressed).toHaveTextContent("Regression");
    expect(regressed).toHaveTextContent("1/2 checks");
    expect(regressed).toHaveTextContent("3.40s");
    expect(regressed).toHaveTextContent("$0.0040");
    expect(within(caseCard("small_talk")).getByText("Fixed")).toBeInTheDocument();
    expect(caseCard("reschedule")).toHaveTextContent("New case");
  });

  it("opens failing cases by default and expands a passing case on click", async () => {
    const user = userEvent.setup();
    mockEvals();
    renderApp(`/evals/${RUN_ID}`);
    await screen.findByRole("region", { name: "Selected eval run" });
    await within(panel()).findByRole("heading", { level: 2, name: /support_agent/ });

    const regressed = caseCard("refund_over_limit");
    const toggle = within(regressed).getByRole("button", { name: /refund_over_limit/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(regressed).getByText("I've issued the refund.")).toBeVisible();
    const checks = within(regressed).getByRole("region", {
      name: "Checks for refund_over_limit",
    });
    // Every check is listed, passing and failing, with the failure's detail.
    expect(checks).toHaveTextContent("tool_called: issue_refund");
    expect(checks).toHaveTextContent("contains: colleague");
    expect(checks).toHaveTextContent("output doesn't mention a colleague");
    expect(regressed).toHaveTextContent("Baseline passed → now fails");
    expect(within(regressed).getByRole("link", { name: "Open trace →" })).toHaveAttribute(
      "href",
      `/traces/${BLOCKED_TRACE_ID}`,
    );

    const ok = caseCard("order_status");
    const okToggle = within(ok).getByRole("button", { name: /order_status/ });
    expect(okToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(ok).getByText("Your order 1042 was delivered on Sep 22.")).not.toBeVisible();
    await user.click(okToggle);
    expect(okToggle).toHaveAttribute("aria-expanded", "true");
    expect(within(ok).getByText("Your order 1042 was delivered on Sep 22.")).toBeVisible();
    expect(within(ok).getByRole("link", { name: "Open trace →" })).toHaveAttribute(
      "href",
      `/traces/${SUPPORT_TRACE_ID}`,
    );
    await user.click(okToggle);
    expect(okToggle).toHaveAttribute("aria-expanded", "false");

    // No output and no trace: said plainly, no link.
    const fresh = caseCard("reschedule");
    expect(within(fresh).getByText("No output recorded.")).toBeVisible();
    expect(within(fresh).queryByRole("link", { name: "Open trace →" })).not.toBeInTheDocument();
  });

  it("says when a run doesn't exist", async () => {
    mockEvals({ run: () => new HttpError(404, { detail: "eval run not found" }) });
    renderApp("/evals/nope");
    expect(await screen.findByText("This eval run doesn't exist")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All runs" })).toHaveAttribute("href", "/evals");
  });
});
