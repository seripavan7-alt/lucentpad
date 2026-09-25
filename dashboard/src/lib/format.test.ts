import { describe, expect, it } from "vitest";
import {
  clientLabel,
  formatCost,
  traceOrigin,
  formatDuration,
  formatRelative,
  formatTokens,
} from "./format";

describe("formatCost", () => {
  it.each([
    [0, "$0.00"],
    [0.00004, "<$0.0001"],
    [0.0054, "$0.0054"],
    [0.02061, "$0.0206"],
    [0.5, "$0.5000"],
    [1.0058, "$1.01"],
    [1234.5, "$1,234.50"],
  ])("%s -> %s", (usd, expected) => {
    expect(formatCost(usd)).toBe(expected);
  });
});

describe("formatTokens", () => {
  it.each([
    [0, "0"],
    [842, "842"],
    [1000, "1k"],
    [4050, "4.1k"],
    [48_200, "48.2k"],
    [100_100, "100k"],
    [1_250_000, "1.3M"],
  ])("%s -> %s", (n, expected) => {
    expect(formatTokens(n)).toBe(expected);
  });
});

describe("formatDuration", () => {
  it.each([
    [0, "0ms"],
    [0.4, "<1ms"],
    [312, "312ms"],
    [1240, "1.24s"],
    [12_340, "12.3s"],
    [95_000, "1m 35s"],
    [3_720_000, "1h 02m"],
  ])("%s -> %s", (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-09-25T12:00:00Z");
  it.each([
    ["2026-09-25T11:59:58Z", "just now"],
    ["2026-09-25T11:59:18Z", "42s ago"],
    ["2026-09-25T11:55:00Z", "5m ago"],
    ["2026-09-25T09:00:00Z", "3h ago"],
    ["2026-09-23T12:00:00Z", "2d ago"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatRelative(iso, now)).toBe(expected);
  });
});

describe("clientLabel", () => {
  it("labels known clients and passes unknown ones through", () => {
    expect(clientLabel("claude-code")).toBe("Claude Code");
    expect(clientLabel("my-bot")).toBe("my-bot");
    expect(clientLabel(null)).toBeNull();
  });
});

describe("traceOrigin", () => {
  it("uses the service name for SDK traces and the client label for gateway traces", () => {
    expect(traceOrigin({ source: "sdk", client: "sdk", service_name: "support-agent" })).toBe(
      "support-agent",
    );
    expect(traceOrigin({ source: "gateway", client: "claude-code", service_name: null })).toBe(
      "Claude Code",
    );
    expect(traceOrigin({ source: "sdk", client: null, service_name: null })).toBeNull();
  });
});
