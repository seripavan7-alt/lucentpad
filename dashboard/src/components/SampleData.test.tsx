import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { setHideSamplePreference } from "../lib/samplePreference";
import { traceListPage1 } from "../test/fixtures";
import { mockFetch, renderApp } from "../test/render";

const mixed = { sample_data: true, real_data: true };
const sampleOnly = { sample_data: true, real_data: false };

function routes(data: object) {
  return {
    "/v1/data": () => data,
    "/v1/traces": () => ({
      ...traceListPage1,
      traces: traceListPage1.traces.map((t, i) => ({ ...t, sample: i > 0 })),
    }),
  };
}

afterEach(() => {
  setHideSamplePreference(false);
});

describe("sample data", () => {
  it("shows no switch and no tags while the database holds only sample data", async () => {
    mockFetch(routes(sampleOnly));
    renderApp("/traces");
    await screen.findByRole("table");
    expect(screen.queryByRole("switch", { name: "Hide sample data" })).not.toBeInTheDocument();
    expect(screen.queryByText("Sample")).not.toBeInTheDocument();
  });

  it("tags sample rows and hides them with the switch once real data exists", async () => {
    const mock = mockFetch(routes(mixed));
    const user = userEvent.setup();
    renderApp("/traces");
    const table = await screen.findByRole("table");
    await waitFor(() => {
      expect(within(table).getAllByText("Sample")).toHaveLength(2);
    });
    const toggle = await screen.findByRole("switch", { name: "Hide sample data" });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    expect(toggle).toBeChecked();
    await waitFor(() => {
      const last = mock
        .urls()
        .filter((u) => u.pathname === "/v1/traces")
        .at(-1)!;
      expect(last.searchParams.get("hide_sample")).toBe("true");
    });
    await waitFor(() => {
      const facets = mock
        .urls()
        .filter((u) => u.pathname === "/v1/traces/facets")
        .at(-1)!;
      expect(facets.searchParams.get("hide_sample")).toBe("true");
    });
  });
});
