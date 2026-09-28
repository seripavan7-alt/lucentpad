import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GuardrailEventList, GuardrailRules, GuardrailSummary } from "../api/types";
import { setHideSamplePreference } from "../lib/samplePreference";
import { BLOCKED_TRACE_ID, SUPPORT_TRACE_ID, supportDetail } from "../test/fixtures";
import {
  blockEvent,
  budgetEvent,
  eventsPage1,
  eventsPage2,
  guardrailSummary,
  M3_NOW,
  noEvents,
  redactionEvent,
  rules,
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

function mockGuardrails({
  events = (url: URL) => (url.searchParams.get("cursor") ? eventsPage2 : eventsPage1),
  rulesBody = () => rules,
  summary = () => guardrailSummary,
  data = { sample_data: true, real_data: false },
}: {
  events?: (url: URL) => GuardrailEventList | HttpError;
  rulesBody?: () => GuardrailRules | HttpError;
  summary?: () => GuardrailSummary | HttpError;
  data?: object;
} = {}) {
  return mockFetch({
    "/v1/guardrails/events": events,
    "/v1/guardrails/rules": rulesBody,
    "/v1/guardrails/summary": summary,
    "/v1/data": () => data,
    "/v1/traces/:id": () => supportDetail,
  });
}

const eventRequests = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/guardrails/events" && !u.searchParams.has("since"));
const livePolls = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/guardrails/events" && u.searchParams.has("since"));
const summaryRequests = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/guardrails/summary");
const eventRows = () => [...document.querySelectorAll<HTMLElement>("tr[data-event-key]")];
const ruleRow = (id: string) => document.querySelector<HTMLElement>(`tr[data-rule="${id}"]`)!;
const tile = (name: string) =>
  within(screen.getByRole("region", { name: "Counts for the range" })).getByRole("group", { name });

describe("Guardrails page", () => {
  it("lists the active rules: what each matches, its message and its blocks in the range", async () => {
    mockGuardrails();
    renderApp("/guardrails");
    expect(screen.getByRole("heading", { level: 1, name: "Guardrails" })).toBeInTheDocument();
    await screen.findByRole("rowheader", { name: "refund_limit" });
    const refund = ruleRow("refund_limit");
    expect(refund).toHaveTextContent("Tool");
    expect(refund).toHaveTextContent("issue_refund when amount > 200");
    expect(refund).toHaveTextContent("Refunds over $200 need a human agent.");
    await waitFor(() => {
      expect(within(refund).getAllByRole("cell").at(-1)).toHaveTextContent("3");
    });
    const legal = ruleRow("no_legal_advice");
    expect(legal).toHaveTextContent("Prompt");
    expect(legal).toHaveTextContent("prompt mentions “lawsuit”, “sue”");
    expect(within(legal).getAllByRole("cell").at(-1)).toHaveTextContent("0");
    // A rule that blocked in the range but is gone from the file is still counted.
    expect(ruleRow("old_rule")).toHaveTextContent("No longer in the rules");
    expect(screen.getByText("/etc/lucentpad/rules.yaml", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("3f2a9c1be04d")).toBeInTheDocument();
  });

  it("shows the range's counts: blocks and values redacted by kind", async () => {
    mockGuardrails();
    renderApp("/guardrails");
    await waitFor(() => {
      expect(tile("Blocks")).toHaveTextContent("4");
    });
    expect(tile("Blocks")).toHaveTextContent("by 2 rules");
    expect(tile("Values redacted")).toHaveTextContent("14");
    expect(tile("Values redacted")).toHaveTextContent("12 emails · 2 API keys");
    expect(tile("Budget alerts")).toHaveTextContent("2");
    expect(tile("Budget alerts")).toHaveTextContent("runs or sessions over budget");
  });

  it("quiets the budget alerts tile when none fired", async () => {
    mockGuardrails({ summary: () => ({ ...guardrailSummary, budget_alerts: 0 }) });
    renderApp("/guardrails");
    await waitFor(() => {
      expect(tile("Budget alerts")).toHaveTextContent("no budget exceeded");
    });
    expect(tile("Budget alerts")).toHaveAttribute("data-empty", "true");
  });

  it("lists budget alerts with spend against the limit and their scope", async () => {
    mockGuardrails({
      events: () => ({
        ...noEvents,
        events: [
          budgetEvent(1, 5),
          budgetEvent(2, 6, {
            source: "gateway",
            client: "claude-code",
            budget_scope: null,
            budget_spent_usd: 2.35,
            budget_limit_usd: 2,
          }),
        ],
      }),
    });
    renderApp("/guardrails");
    await waitFor(() => {
      expect(eventRows()).toHaveLength(2);
    });
    const [run, session] = eventRows();
    expect(run).toHaveAttribute("data-kind", "budget");
    expect(within(run!).getAllByRole("cell")[1]).toHaveTextContent("Budget");
    expect(run).toHaveTextContent("Budget alert · spent $0.6200 of $0.5000 (run)");
    expect(within(run!).getByRole("link", { name: SUPPORT_TRACE_ID.slice(0, 8) })).toHaveAttribute(
      "href",
      `/traces/${SUPPORT_TRACE_ID}?span=0000000000000005`,
    );
    // No scope recorded: a gateway alert covers a session.
    expect(session).toHaveTextContent("Budget alert · spent $2.35 of $2.00 (session)");
    expect(session).toHaveTextContent("Gateway · Claude Code");
  });

  it("filters to budget alerts", async () => {
    const mock = mockGuardrails();
    const user = userEvent.setup();
    renderApp("/guardrails");
    await waitFor(() => {
      expect(eventRows()).toHaveLength(3);
    });
    const kinds = within(screen.getByRole("group", { name: "Event kind" }));
    expect(kinds.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "All",
      "Blocks",
      "Redactions",
      "Budget",
    ]);
    await user.click(kinds.getByRole("button", { name: "Budget" }));
    expect(getLocation()).toBe("/guardrails?kind=budget");
    await waitFor(() => {
      expect(eventRequests(mock).at(-1)!.searchParams.getAll("kind")).toEqual(["budget"]);
    });
  });

  it("says when no budget alerts fired in the range", async () => {
    mockGuardrails({ events: () => noEvents });
    renderApp("/guardrails?kind=budget");
    expect(await screen.findByText("No budget alerts in the last 24 hours")).toBeInTheDocument();
  });

  it("lists events newest first, each opening its span in the trace", async () => {
    const mock = mockGuardrails();
    renderApp("/guardrails");
    await waitFor(() => {
      expect(eventRows()).toHaveLength(3);
    });
    const [block, redaction] = eventRows();
    expect(block).toHaveAttribute("data-kind", "block");
    expect(block).toHaveTextContent("Blocked");
    expect(block).toHaveTextContent("refund_limit · refund of $489 exceeds the $200 limit");
    expect(block).toHaveTextContent("SDK");
    expect(
      within(block!).getByRole("link", { name: BLOCKED_TRACE_ID.slice(0, 8) }),
    ).toHaveAttribute("href", `/traces/${BLOCKED_TRACE_ID}?span=0000000000000001`);
    expect(redaction).toHaveTextContent("Redacted");
    expect(redaction).toHaveTextContent("Email × 2");
    expect(redaction).toHaveTextContent("Gateway · Claude Code");

    const req = eventRequests(mock)[0]!;
    expect(Date.parse(req.searchParams.get("from")!)).toBe(M3_NOW - 86_400_000);
    expect(req.searchParams.getAll("kind")).toEqual([]);
    expect(Date.parse(summaryRequests(mock)[0]!.searchParams.get("from")!)).toBe(
      M3_NOW - 86_400_000,
    );
  });

  it("opens the trace from a row", async () => {
    mockGuardrails();
    const user = userEvent.setup();
    renderApp("/guardrails");
    await waitFor(() => {
      expect(eventRows()).toHaveLength(3);
    });
    await user.click(eventRows()[0]!.querySelector("td")!);
    expect(getLocation()).toBe(`/traces/${BLOCKED_TRACE_ID}?span=0000000000000001`);
  });

  it("filters by kind in the URL and loads more with the cursor", async () => {
    const mock = mockGuardrails();
    const user = userEvent.setup();
    renderApp("/guardrails");
    await waitFor(() => {
      expect(eventRows()).toHaveLength(3);
    });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(eventRows()).toHaveLength(4);
    });
    expect(eventRequests(mock).at(-1)!.searchParams.get("cursor")).toBe("e2.x");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();

    await user.click(
      within(screen.getByRole("group", { name: "Event kind" })).getByRole("button", {
        name: "Blocks",
      }),
    );
    expect(getLocation()).toBe("/guardrails?kind=block");
    await waitFor(() => {
      expect(eventRequests(mock).at(-1)!.searchParams.getAll("kind")).toEqual(["block"]);
    });
  });

  it("offers to clear the kind filter or widen the range when nothing matches", async () => {
    mockGuardrails({ events: () => noEvents });
    const user = userEvent.setup();
    renderApp("/guardrails?kind=redaction");
    expect(await screen.findByText("No redactions in the last 24 hours")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show all events" }));
    expect(getLocation()).toBe("/guardrails");
    expect(await screen.findByText("No guardrail events in the last 24 hours")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show last 30 days" }));
    expect(getLocation()).toBe("/guardrails?range=30d");
  });

  it("says the rules aren't loaded when the server has none (501), and still lists events", async () => {
    mockGuardrails({ rulesBody: () => new HttpError(501, { detail: "guardrails not loaded" }) });
    renderApp("/guardrails");
    expect(await screen.findByText("Rules aren't loaded")).toBeInTheDocument();
    await waitFor(() => {
      expect(eventRows()).toHaveLength(3);
    });
  });

  it("explains a server without guardrail queries (501)", async () => {
    const notYet = () => new HttpError(501, { detail: "not implemented yet" });
    mockGuardrails({ events: notYet, summary: notYet });
    renderApp("/guardrails");
    expect(await screen.findByText("Guardrail events aren't available yet")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Counts for the range" })).not.toBeInTheDocument();
  });

  it("says so when no rules are configured", async () => {
    mockGuardrails({
      rulesBody: () => ({ ...rules, rules: [] }),
      summary: () => ({ ...guardrailSummary, blocks: [] }),
    });
    renderApp("/guardrails");
    expect(await screen.findByText("No blocking rules")).toBeInTheDocument();
  });

  it("leaves sample data out when the sidebar switch says so", async () => {
    setHideSamplePreference(true);
    const mock = mockGuardrails({ data: { sample_data: true, real_data: true } });
    renderApp("/guardrails");
    await waitFor(() => {
      expect(eventRequests(mock).at(-1)!.searchParams.get("hide_sample")).toBe("true");
      expect(summaryRequests(mock).at(-1)!.searchParams.get("hide_sample")).toBe("true");
    });
  });
});

describe("Guardrails page, live", () => {
  const AS_OF_2 = "2026-09-25T10:10:03Z";

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(M3_NOW);
  });

  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  it("polls every 3 s with since, prepends new events with a highlight and refreshes the counts", async () => {
    const arrived = blockEvent(0, 9);
    const mock = mockGuardrails({
      events: (url) => {
        if (url.searchParams.has("since")) {
          // The poll repeats a known event (the API's overlap) next to the new one.
          return { events: [arrived, eventsPage1.events[0]!], next_cursor: null, as_of: AS_OF_2 };
        }
        return url.searchParams.get("cursor") ? eventsPage2 : eventsPage1;
      },
    });
    renderApp("/guardrails?kind=block");
    await waitFor(() => {
      expect(eventRows()).toHaveLength(3);
    });
    const summariesBefore = summaryRequests(mock).length;
    expect(livePolls(mock)).toHaveLength(0);

    await tick(3000);
    await waitFor(() => {
      expect(eventRows()).toHaveLength(4);
    });
    const poll = livePolls(mock)[0]!;
    expect(poll.searchParams.get("since")).toBe(eventsPage1.as_of);
    expect(poll.searchParams.getAll("kind")).toEqual(["block"]);
    expect(poll.searchParams.has("cursor")).toBe(false);
    const [first, second] = eventRows();
    expect(first).toHaveAttribute("data-fresh", "true");
    expect(first!.dataset.eventKey).toContain("0000000000000009");
    expect(second).not.toHaveAttribute("data-fresh");
    await waitFor(() => {
      expect(summaryRequests(mock).length).toBeGreaterThan(summariesBefore);
    });

    await tick(3000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(2);
    });
    expect(livePolls(mock)[1]!.searchParams.get("since")).toBe(AS_OF_2);
    expect(eventRows()).toHaveLength(4); // not duplicated
  });

  it("replaces the empty state when the first event arrives", async () => {
    let live: GuardrailEventList = noEvents;
    mockGuardrails({ events: (url) => (url.searchParams.has("since") ? live : noEvents) });
    renderApp("/guardrails");
    await screen.findByText("No guardrail events in the last 24 hours");
    live = { events: [redactionEvent(0, 7)], next_cursor: null, as_of: AS_OF_2 };
    await tick(3000);
    await waitFor(() => {
      expect(eventRows()).toHaveLength(1);
    });
  });
});
