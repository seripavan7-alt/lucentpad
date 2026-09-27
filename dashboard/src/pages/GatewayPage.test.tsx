import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GatewaySummary, GatewayTurnList } from "../api/types";
import {
  CC_SESSION,
  CHAT_SESSION,
  CLI_SESSION,
  emptySummary,
  emptyTurns,
  gatewaySummary,
  turn,
  turnsPage1,
  turnsPage2,
} from "../test/gatewayFixtures";
import { BASE_TIME, supportDetail } from "../test/fixtures";
import { getLocation, HttpError, mockFetch, renderApp } from "../test/render";
import { CLAUDE_CODE_SETUP, COPILOT_CLI_SETUP, OPENAI_BASE } from "../features/gateway/setup";

const NOW = BASE_TIME + 10 * 60_000;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

type Mock = ReturnType<typeof mockFetch>;

const turnRequests = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/gateway/turns" && !u.searchParams.has("since"));
const livePolls = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/gateway/turns" && u.searchParams.has("since"));
const summaryRequests = (mock: Mock) =>
  mock.urls().filter((u) => u.pathname === "/v1/gateway/summary");
const fromOf = (url: URL) => Date.parse(url.searchParams.get("from") ?? "");

function mockGateway({
  turns = (url: URL) => (url.searchParams.get("cursor") ? turnsPage2 : turnsPage1),
  summary = () => gatewaySummary,
}: {
  turns?: (url: URL) => GatewayTurnList | HttpError;
  summary?: (url: URL) => GatewaySummary;
} = {}) {
  return mockFetch({
    "/v1/gateway/turns": turns,
    "/v1/gateway/summary": summary,
    "/v1/traces/:id": () => supportDetail,
  });
}

const sessions = () => [...document.querySelectorAll<HTMLElement>("tbody[data-session-id]")];
const turnRows = () => [...document.querySelectorAll<HTMLElement>("tr[data-span-id]")];
const tile = (name: string) =>
  within(screen.getByRole("region", { name: "Totals by client" })).getByRole("group", { name });

describe("Gateway page", () => {
  it("shows per-client totals in a fixed order, zeros included", async () => {
    mockGateway();
    renderApp("/gateway");
    expect(screen.getByRole("heading", { level: 1, name: "Gateway" })).toBeInTheDocument();
    await screen.findByRole("table");

    const totals = screen.getByRole("region", { name: "Totals by client" });
    const names = within(totals)
      .getAllByRole("group")
      .map((g) => g.getAttribute("aria-label"));
    expect(names).toEqual(["Claude Code", "Copilot Chat", "Copilot CLI", "Other"]);
    await waitFor(() => {
      expect(tile("Claude Code")).toHaveTextContent("$3.22");
    });
    expect(tile("Claude Code")).toHaveTextContent("3 sessions · 41 turns");
    expect(tile("Claude Code")).toHaveTextContent("1.3M in · 48.2k out");
    expect(tile("Copilot CLI")).toHaveTextContent("1 session · 6 turns");
    expect(tile("Copilot Chat")).toHaveTextContent("$0.0945");
    expect(tile("Other")).toHaveTextContent("$0.00");
    expect(tile("Other")).toHaveTextContent("0 sessions · 0 turns");
    expect(tile("Other")).toHaveAttribute("data-empty", "true");
  });

  it("groups turns by session, newest first, with a header per session", async () => {
    mockGateway();
    renderApp("/gateway");
    await screen.findByRole("table");

    const groups = sessions();
    expect(groups.map((g) => g.dataset.sessionId)).toEqual([CC_SESSION, CHAT_SESSION]);
    const cc = within(groups[0]!);
    const header = within(cc.getAllByRole("row")[0]!);
    expect(header.getByRole("link", { name: "Claude Code" })).toHaveAttribute(
      "href",
      `/traces/${CC_SESSION}`,
    );
    expect(header.getByText("3 turns")).toBeInTheDocument();
    // Started = the oldest loaded turn (09:56 UTC); cost = sum of known turn costs.
    expect(header.getByText("Sep 25, 09:56:00")).toBeInTheDocument();
    expect(header.getByText("$0.0401")).toBeInTheDocument();
    expect(cc.getAllByRole("row")).toHaveLength(4);

    const chat = within(groups[1]!);
    expect(chat.getByRole("link", { name: "Copilot Chat" })).toBeInTheDocument();
    expect(chat.getByText("1 turn")).toBeInTheDocument();

    // Turn cells: model, failover tag, tokens, TTFB / duration, cost, status, preview.
    const row = (spanId: string) => turnRows().find((r) => r.dataset.spanId === spanId);
    const latest = row("aa00000000000003");
    const chatTurn = row("bb00000000000002");
    const errored = row("aa00000000000002");
    expect(within(latest!).getByText("claude-haiku-4-5")).toBeInTheDocument();
    expect(within(latest!).getByText("failover")).toBeInTheDocument();
    expect(latest).toHaveTextContent("12k / 400");
    expect(latest).toHaveTextContent("380ms / 4.20s");
    expect(latest).toHaveTextContent("$0.0121");
    expect(within(latest!).getByTestId("turn-preview")).toHaveTextContent(
      "Run the tests again. → All 42 tests pass.",
    );
    expect(within(chatTurn!).queryByText("failover")).not.toBeInTheDocument();
    expect(chatTurn).toHaveTextContent("4.20s");
    expect(chatTurn).not.toHaveTextContent("/ 4.20s"); // no TTFB
    expect(within(errored!).getByText("Error")).toBeInTheDocument();
  });

  it("asks for the last 24 hours by default and switches range through the URL", async () => {
    const mock = mockGateway();
    const user = userEvent.setup();
    renderApp("/gateway");
    await screen.findByRole("table");
    const first = turnRequests(mock)[0]!;
    expect(fromOf(first)).toBe(NOW - 86_400_000);
    expect(first.searchParams.get("limit")).toBe("50");
    expect(first.searchParams.has("client")).toBe(false);
    expect(fromOf(summaryRequests(mock)[0]!)).toBe(NOW - 86_400_000);

    const range = screen.getByRole("group", { name: "Time range" });
    expect(within(range).getByRole("button", { name: "24h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(within(range).getByRole("button", { name: "1h" }));
    expect(getLocation()).toBe("/gateway?range=1h");
    await waitFor(() => {
      expect(fromOf(turnRequests(mock).at(-1)!)).toBe(NOW - 3600_000);
    });
    await waitFor(() => {
      expect(fromOf(summaryRequests(mock).at(-1)!)).toBe(NOW - 3600_000);
    });
  });

  it("filters by client: URL, request params and the marked tile", async () => {
    const mock = mockGateway();
    const user = userEvent.setup();
    renderApp("/gateway?range=4h");
    await screen.findByRole("table");

    const clients = screen.getByRole("group", { name: "Client" });
    expect(within(clients).getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(within(clients).getByRole("button", { name: "Copilot CLI" }));
    expect(getLocation()).toBe("/gateway?range=4h&client=copilot-cli");
    await waitFor(() => {
      expect(turnRequests(mock).at(-1)!.searchParams.getAll("client")).toEqual(["copilot-cli"]);
    });
    expect(tile("Copilot CLI")).toHaveAttribute("data-selected", "true");
    // Totals cover every client: the summary is not refetched per client.
    expect(summaryRequests(mock).every((u) => !u.searchParams.has("client"))).toBe(true);

    await user.click(within(clients).getByRole("button", { name: "All" }));
    expect(getLocation()).toBe("/gateway?range=4h");
  });

  it("reads the client filter from the URL and offers all clients when it's empty", async () => {
    const mock = mockGateway({ turns: () => emptyTurns });
    const user = userEvent.setup();
    renderApp("/gateway?client=other");
    expect(
      await screen.findByText("No turns from other clients in the last 24 hours"),
    ).toBeVisible();
    expect(turnRequests(mock)[0]!.searchParams.getAll("client")).toEqual(["other"]);
    await user.click(screen.getByRole("button", { name: "Show all clients" }));
    expect(getLocation()).toBe("/gateway");
  });

  it("loads more turns with the cursor and the first page's window", async () => {
    const mock = mockGateway();
    const user = userEvent.setup();
    renderApp("/gateway");
    await screen.findByRole("table");
    vi.setSystemTime(NOW + 5000);
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => {
      expect(sessions()).toHaveLength(3);
    });
    const [first, second] = turnRequests(mock);
    expect(second!.searchParams.get("cursor")).toBe("cursor-2");
    expect(second!.searchParams.get("from")).toBe(first!.searchParams.get("from"));
    expect(sessions()[2]!.dataset.sessionId).toBe(CLI_SESSION);
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("opens the session trace from a header, and the turn's span from a turn", async () => {
    mockGateway();
    const user = userEvent.setup();
    renderApp("/gateway");
    await screen.findByRole("table");
    await user.click(
      turnRows()
        .find((r) => r.dataset.spanId === "bb00000000000002")!
        .querySelector("td:nth-child(3)")!,
    );
    expect(getLocation()).toBe(`/traces/${CHAT_SESSION}?span=bb00000000000002`);
  });

  it("opens the session trace from the session header row", async () => {
    mockGateway();
    const user = userEvent.setup();
    renderApp("/gateway");
    await screen.findByRole("table");
    await user.click(within(sessions()[0]!).getAllByRole("row")[0]!.querySelector("td")!);
    expect(getLocation()).toBe(`/traces/${CC_SESSION}`);
  });

  it("shows an error with retry when turns can't load", async () => {
    let fail = true;
    mockGateway({ turns: () => (fail ? new HttpError(500, { detail: "boom" }) : turnsPage1) });
    const user = userEvent.setup();
    renderApp("/gateway");
    expect(await screen.findByText("Couldn't load gateway turns")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("drops the milestone tag from the Gateway nav item", () => {
    mockGateway();
    renderApp("/gateway");
    const nav = screen.getByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: /Gateway/ });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(link).toHaveTextContent(/^Gateway$/);
  });
});

describe("Gateway page, no traffic", () => {
  it("shows each client's setup with working copy buttons", async () => {
    mockGateway({ turns: () => emptyTurns, summary: () => emptySummary });
    const user = userEvent.setup();
    renderApp("/gateway");
    expect(
      await screen.findByRole("heading", { name: "No gateway traffic in the last 24 hours" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Totals by client" })).not.toBeInTheDocument();

    const claude = screen.getByRole("region", { name: "Claude Code" });
    expect(claude).toHaveTextContent("ANTHROPIC_BASE_URL=http://localhost:8000/gateway/anthropic");
    const cli = screen.getByRole("region", { name: "Copilot CLI" });
    for (const line of [
      "COPILOT_PROVIDER_TYPE=anthropic",
      "COPILOT_PROVIDER_BASE_URL=http://localhost:8000/gateway/anthropic",
      "COPILOT_PROVIDER_API_KEY=",
      "COPILOT_MODEL=claude-sonnet-5",
    ]) {
      expect(cli).toHaveTextContent(line);
    }
    const chat = screen.getByRole("region", { name: "Copilot Chat in VS Code" });
    expect(chat).toHaveTextContent("Manage Models");
    expect(chat).toHaveTextContent("Custom Endpoint");
    expect(chat).toHaveTextContent("http://localhost:8000/gateway/openai/v1");
    expect(chat).toHaveTextContent("your OpenAI key");
    expect(screen.getByText(/traced only when it uses your own key \(BYOK\)/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Copy Claude Code setup" }));
    expect(await navigator.clipboard.readText()).toBe(CLAUDE_CODE_SETUP);
    expect(screen.getByRole("button", { name: "Copy Claude Code setup" })).toHaveTextContent(
      "Copied",
    );
    await user.click(screen.getByRole("button", { name: "Copy Copilot CLI setup" }));
    expect(await navigator.clipboard.readText()).toBe(COPILOT_CLI_SETUP);
    await user.click(screen.getByRole("button", { name: "Copy Copilot Chat base URL" }));
    expect(await navigator.clipboard.readText()).toBe(OPENAI_BASE);
  });

  it("offers a wider range", async () => {
    mockGateway({ turns: () => emptyTurns, summary: () => emptySummary });
    const user = userEvent.setup();
    renderApp("/gateway?range=1h");
    await user.click(await screen.findByRole("button", { name: "Show last 24 hours" }));
    expect(getLocation()).toBe("/gateway");
  });
});

describe("Gateway page, live", () => {
  const AS_OF_2 = "2026-09-25T10:10:03Z";
  const arrived = turn("aa00000000000004", CC_SESSION, 1000, {
    input_preview: "Commit it.",
    output_preview: "Committed.",
  });

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
  });

  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

  function liveMock(poll: (url: URL) => GatewayTurnList | HttpError) {
    return mockGateway({
      turns: (url) => {
        if (url.searchParams.has("since")) return poll(url);
        return url.searchParams.get("cursor") ? turnsPage2 : turnsPage1;
      },
    });
  }

  it("polls every 3 s with since, prepends new turns with a highlight and refreshes totals", async () => {
    // The poll repeats one known turn (the API's overlap) next to the new one.
    const mock = liveMock(() => ({
      turns: [arrived, turnsPage1.turns[0]!],
      next_cursor: null,
      as_of: AS_OF_2,
    }));
    renderApp("/gateway?client=claude-code");
    await screen.findByRole("table");
    const summariesBefore = summaryRequests(mock).length;
    expect(livePolls(mock)).toHaveLength(0);

    await tick(3000);
    await waitFor(() => {
      expect(turnRows()).toHaveLength(5);
    });
    const poll = livePolls(mock)[0]!;
    expect(poll.searchParams.get("since")).toBe(turnsPage1.as_of);
    expect(poll.searchParams.getAll("client")).toEqual(["claude-code"]);
    expect(poll.searchParams.has("cursor")).toBe(false);
    expect(Math.abs(Date.now() - 86_400_000 - fromOf(poll))).toBeLessThan(5000);

    const [first, second] = turnRows();
    expect(first).toHaveAttribute("data-span-id", "aa00000000000004");
    expect(first).toHaveAttribute("data-fresh", "true");
    expect(second).not.toHaveAttribute("data-fresh");
    expect(within(sessions()[0]!).getByText("4 turns")).toBeInTheDocument();
    await waitFor(() => {
      expect(summaryRequests(mock).length).toBeGreaterThan(summariesBefore);
    });

    await tick(3000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(2);
    });
    expect(livePolls(mock)[1]!.searchParams.get("since")).toBe(AS_OF_2);
    expect(turnRows()).toHaveLength(5); // not duplicated
    await waitFor(() => {
      expect(turnRows()[0]).not.toHaveAttribute("data-fresh");
    });
  });

  it("keeps polling from the empty state, so the first turn replaces the setup", async () => {
    let live: GatewayTurnList = emptyTurns;
    mockGateway({
      turns: (url) => (url.searchParams.has("since") ? live : emptyTurns),
      summary: () => emptySummary,
    });
    renderApp("/gateway");
    await screen.findByRole("heading", { name: /No gateway traffic/ });
    live = { turns: [arrived], next_cursor: null, as_of: AS_OF_2 };
    await tick(3000);
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(turnRows()).toHaveLength(1);
  });

  it("pauses in hidden tabs and backs off on errors", async () => {
    let state: DocumentVisibilityState = "hidden";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
    let fail = true;
    const mock = liveMock(() => (fail ? new HttpError(503, { detail: "down" }) : emptyTurns));
    renderApp("/gateway");
    await screen.findByRole("table");

    await tick(10_000);
    expect(livePolls(mock)).toHaveLength(0);
    state = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(1); // fails → next in 6 s
    });
    await tick(5000);
    expect(livePolls(mock)).toHaveLength(1);
    fail = false;
    await tick(1000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(2);
    });
    await tick(3000);
    await waitFor(() => {
      expect(livePolls(mock)).toHaveLength(3);
    });
    expect(turnRows()).toHaveLength(4); // the feed stays up
  });

  it("starts over when a poll returns a full page", async () => {
    const many = Array.from({ length: 200 }, (_, i) =>
      turn(`dd${String(i).padStart(14, "0")}`, CC_SESSION, 1000 + i),
    );
    const mock = liveMock(() => ({ turns: many, next_cursor: null, as_of: AS_OF_2 }));
    renderApp("/gateway");
    await screen.findByRole("table");
    await tick(3000);
    await waitFor(() => {
      expect(turnRequests(mock)).toHaveLength(2);
    });
    expect(turnRows()).toHaveLength(4); // refetched page 1, not merged
  });
});
