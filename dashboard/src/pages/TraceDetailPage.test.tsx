import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
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
});
