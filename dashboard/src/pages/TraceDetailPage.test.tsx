import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Span, TraceDetail } from "../api/types";
import {
  at,
  BASE_TIME,
  LONG_INPUT,
  supportSpans,
  supportSummary,
  BLOCKED_TRACE_ID,
  CLAUDE_CODE_TRACE_ID,
  SUPPORT_TRACE_ID,
  blockedDetail,
  claudeCodeDetail,
  supportDetail,
} from "../test/fixtures";
import { getLocation, HttpError, mockFetch, renderApp } from "../test/render";

function mockDetails() {
  return mockFetch({
    [`/v1/traces/${SUPPORT_TRACE_ID}`]: () => supportDetail,
    [`/v1/traces/${BLOCKED_TRACE_ID}`]: () => blockedDetail,
    [`/v1/traces/${CLAUDE_CODE_TRACE_ID}`]: () => claudeCodeDetail,
    "/v1/traces/:id": () => new HttpError(404, { detail: "Trace not found" }),
  });
}

describe("Trace detail", () => {
  it("renders summary stats and one waterfall row per span, in tree order", async () => {
    mockDetails();
    renderApp(`/traces/${SUPPORT_TRACE_ID}`);

    const list = await screen.findByRole("list", { name: "Spans" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((r) => r.dataset.depth)).toEqual(["0", "1", "1", "1", "1", "2", "1"]);
    expect(rows[5]).toHaveTextContent("payments.refund");
    expect(rows[1]).toHaveAttribute("data-kind", "llm");

    const bars = within(list).getAllByTestId("span-bar");
    expect(bars[0]!.style.left).toBe("0%");
    expect(bars[0]!.style.width).toBe("100%");
    expect(parseFloat(bars[2]!.style.left)).toBeCloseTo((1300 / 4200) * 100, 3);

    expect(screen.getByText("$0.0209")).toBeInTheDocument();
    expect(screen.getByText(SUPPORT_TRACE_ID)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Budget alert at +3.90s" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Redaction at +60ms" })).toBeInTheDocument();
  });

  it("opens the inspector with attributes, events, tokens, cost and latency", async () => {
    mockDetails();
    const user = userEvent.setup();
    renderApp(`/traces/${SUPPORT_TRACE_ID}`);

    const list = await screen.findByRole("list", { name: "Spans" });
    await user.click(within(list).getAllByRole("button")[1]!);
    expect(getLocation()).toBe(`/traces/${SUPPORT_TRACE_ID}?span=a000000000000002`);

    const inspector = screen.getByRole("complementary", { name: "Span details" });
    const i = within(inspector);
    expect(i.getByRole("heading", { name: "chat claude-sonnet-4-5" })).toBeInTheDocument();
    const field = (label: string) => i.getByText(label, { selector: "dt" }).nextElementSibling;
    expect(field("Latency")).toHaveTextContent("1.20s");
    expect(field("Tokens in")).toHaveTextContent("1,840");
    expect(field("Tokens out")).toHaveTextContent("212");
    expect(field("Cost")).toHaveTextContent("$0.0087");
    expect(field("Model")).toHaveTextContent("claude-sonnet-4-5");
    expect(i.getByText("gen_ai.usage.input_tokens")).toBeInTheDocument();
    expect(i.getByText("tool_use")).toBeInTheDocument(); // array attribute
    expect(i.getByText("Redaction")).toBeInTheDocument();
    expect(i.getByText("email")).toBeInTheDocument(); // event attribute

    await user.click(i.getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("complementary", { name: "Span details" })).not.toBeInTheDocument();
    expect(getLocation()).toBe(`/traces/${SUPPORT_TRACE_ID}`);
  });

  it("restores the selected span from the URL and shows redacted values", async () => {
    mockDetails();
    renderApp(`/traces/${SUPPORT_TRACE_ID}?span=a000000000000006`);
    const inspector = await screen.findByRole("complementary", { name: "Span details" });
    expect(within(inspector).getByText("[REDACTED:email]")).toBeInTheDocument();
  });

  it("marks blocked guardrail spans and failover events", async () => {
    mockDetails();
    const user = userEvent.setup();
    renderApp(`/traces/${BLOCKED_TRACE_ID}`);
    const list = await screen.findByRole("list", { name: "Spans" });
    const guardrail = within(list).getAllByRole("button")[2]!;
    expect(guardrail).toHaveAttribute("data-kind", "guardrail");
    expect(guardrail).toHaveAttribute("data-status", "blocked");
    expect(within(guardrail).getByText("Blocked")).toBeInTheDocument();
    expect(within(list).getByRole("img", { name: /^Guardrail block at/ })).toBeInTheDocument();

    await user.click(guardrail);
    expect(screen.getByText("Refund of $900 exceeds the $200 limit")).toBeInTheDocument();
  });

  it("shows a failover marker and requested vs. served model", async () => {
    mockDetails();
    renderApp(`/traces/${CLAUDE_CODE_TRACE_ID}?span=c000000000000003`);
    expect(await screen.findByRole("img", { name: "Failover at +31.2s" })).toBeInTheDocument();
    const inspector = screen.getByRole("complementary", { name: "Span details" });
    expect(inspector).toHaveTextContent("claude-sonnet-4-5 (requested claude-opus-4-1)");
  });

  it("shows not found for an unknown trace", async () => {
    mockDetails();
    renderApp("/traces/ffffffffffffffffffffffffffffffff");
    expect(await screen.findByRole("alert")).toHaveTextContent("Trace not found");
  });

  it("shows the cost of every LLM call on its waterfall row", async () => {
    mockDetails();
    renderApp(`/traces/${SUPPORT_TRACE_ID}`);
    const list = await screen.findByRole("list", { name: "Spans" });
    const rows = within(list).getAllByRole("button");
    const llmRows = rows.filter((r) => r.dataset.kind === "llm");
    expect(llmRows).toHaveLength(2);
    expect(within(llmRows[0]!).getByTestId("span-cost")).toHaveTextContent("$0.0087");
    expect(within(llmRows[1]!).getByTestId("span-cost")).toHaveTextContent("$0.0122");
    const toolRow = rows.find((r) => r.dataset.kind === "tool")!;
    expect(within(toolRow).queryByTestId("span-cost")).not.toBeInTheDocument();
  });

  it("shows Input and Output blocks for LLM spans, collapsed with Show more", async () => {
    mockDetails();
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderApp(`/traces/${SUPPORT_TRACE_ID}?span=a000000000000002`);
    const inspector = await screen.findByRole("complementary", { name: "Span details" });

    const input = within(inspector).getByRole("region", { name: "Input" });
    const text = within(input).getByText(
      (_, el) => el?.textContent === LONG_INPUT && el.tagName === "DIV",
    );
    expect(text).toHaveAttribute("data-collapsed", "true");
    expect(text.textContent).toContain("\n"); // line breaks kept (rendered with pre-wrap)
    expect(within(input).getByText("Truncated at capture")).toBeInTheDocument();
    await user.click(within(input).getByRole("button", { name: "Show more" }));
    expect(text).not.toHaveAttribute("data-collapsed");
    expect(within(input).getByRole("button", { name: "Show less" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await user.click(within(input).getByRole("button", { name: "Copy input" }));
    expect(writeText).toHaveBeenCalledWith(LONG_INPUT);
    expect(await within(input).findByText("Copied")).toBeInTheDocument();

    const output = within(inspector).getByRole("region", { name: "Output" });
    expect(output).toHaveTextContent("Let me look up order 1042.");
    expect(within(output).queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    expect(within(output).queryByText("Truncated at capture")).not.toBeInTheDocument();
    // Not repeated in the attribute list.
    expect(within(inspector).queryByText("lucentpad.input.preview")).not.toBeInTheDocument();
  });

  it("notes when an LLM span has no captured content, and shows no blocks for tools", async () => {
    mockDetails();
    const user = userEvent.setup();
    renderApp(`/traces/${SUPPORT_TRACE_ID}?span=a000000000000004`);
    const inspector = await screen.findByRole("complementary", { name: "Span details" });
    expect(within(inspector).getByText("Input and output not captured")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Spans" });
    await user.click(within(list).getAllByRole("button")[2]!); // lookup_order (tool)
    const tool = screen.getByRole("complementary", { name: "Span details" });
    expect(within(tool).queryByRole("region", { name: "Input" })).not.toBeInTheDocument();
    expect(within(tool).queryByText("Input and output not captured")).not.toBeInTheDocument();
  });
});

describe("Trace detail, live", () => {
  // A support run in progress: the root span (exported when the run ends) hasn't arrived.
  const [root, llm1, lookup, llm2] = supportSpans as [Span, Span, Span, Span];
  const summary = { ...supportSummary, span_count: 1 };
  const START = BASE_TIME;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(START + 1500);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const detail = (spans: Span[], asOf: string): TraceDetail => ({
    trace: summary,
    spans,
    as_of: asOf,
  });
  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
  const polls = (mock: ReturnType<typeof mockFetch>) =>
    mock.urls().filter((u) => u.pathname.startsWith("/v1/traces/") && u.searchParams.has("since"));
  const rows = () => within(screen.getByRole("list", { name: "Spans" })).getAllByRole("button");

  it("appends spans across polls in order, grows the axis, and ends Live at the root span", async () => {
    const replies: TraceDetail[] = [
      detail([lookup], at(1600)),
      detail([llm2], at(3000)),
      detail([llm2, root], at(4300)),
    ];
    let n = 0;
    const mock = mockFetch({
      [`/v1/traces/${SUPPORT_TRACE_ID}`]: (url) =>
        url.searchParams.has("since")
          ? (replies[Math.min(n++, replies.length - 1)] ?? null)
          : detail([llm1], at(1500)),
    });
    renderApp(`/traces/${SUPPORT_TRACE_ID}`);
    await screen.findByRole("list", { name: "Spans" });
    expect(screen.getByRole("status")).toHaveTextContent("Live");
    expect(rows()).toHaveLength(1);
    const width0 = parseFloat(within(rows()[0]!).getByTestId("span-bar").style.width);

    await tick(1000);
    await waitFor(() => {
      expect(rows()).toHaveLength(2);
    });
    expect(polls(mock)[0]!.searchParams.get("since")).toBe(at(1500));
    expect(rows().map((r) => r.textContent)).toEqual([
      expect.stringContaining("chat claude-sonnet-4-5"),
      expect.stringContaining("lookup_order"),
    ]);
    // The axis runs to "now", so the first bar takes a smaller share as time passes.
    const width1 = parseFloat(within(rows()[0]!).getByTestId("span-bar").style.width);
    expect(width1).toBeLessThan(width0);

    await tick(1000);
    await waitFor(() => {
      expect(rows()).toHaveLength(3);
    });
    expect(polls(mock)[1]!.searchParams.get("since")).toBe(at(1600));
    expect(screen.getByRole("status")).toHaveTextContent("Live");

    await tick(1000);
    await waitFor(() => {
      expect(rows()).toHaveLength(4); // llm2 merged by span_id, root added
    });
    expect(rows()[0]).toHaveTextContent("support-agent");
    expect(rows().map((r) => r.dataset.depth)).toEqual(["0", "1", "1", "1"]);
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
    // Root present: polling stops.
    const count = polls(mock).length;
    await tick(5000);
    expect(polls(mock)).toHaveLength(count);
  });

  it("switches Live off after 10 s without new spans, and slows polling", async () => {
    const mock = mockFetch({
      [`/v1/traces/${SUPPORT_TRACE_ID}`]: (url) =>
        detail(url.searchParams.has("since") ? [] : [llm1], at(1500)),
    });
    renderApp(`/traces/${SUPPORT_TRACE_ID}`);
    await screen.findByRole("list", { name: "Spans" });
    expect(screen.getByRole("status")).toHaveTextContent("Live");

    await tick(9000);
    expect(screen.getByRole("status")).toHaveTextContent("Live");
    expect(polls(mock).length).toBeGreaterThanOrEqual(8);
    await tick(2000);
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
    const count = polls(mock).length;
    await tick(4000);
    expect(polls(mock)).toHaveLength(count);
    await tick(1500);
    await waitFor(() => {
      expect(polls(mock)).toHaveLength(count + 1);
    });
  });

  it("does not poll a finished trace", async () => {
    const mock = mockDetails();
    renderApp(`/traces/${SUPPORT_TRACE_ID}`);
    await screen.findByRole("list", { name: "Spans" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await tick(5000);
    expect(polls(mock)).toHaveLength(0);
  });
});
