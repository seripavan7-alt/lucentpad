import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { setFetcher } from "../api/client";
import { AppRoutes } from "../App";
import { createDemoFetch, freshShift, shiftSnapshot, type DemoSnapshot } from "./adapter";
import { DemoModeContext } from "./context";
import snapshotJson from "./snapshot.json";

const snapshot = snapshotJson as unknown as DemoSnapshot;

afterEach(() => {
  setFetcher((url, init) => fetch(url, init));
});

describe("Dashboard in demo mode", () => {
  it("serves traces from the snapshot and shows the demo banner", async () => {
    setFetcher(createDemoFetch(shiftSnapshot(snapshot, freshShift(snapshot))));
    render(
      <DemoModeContext.Provider value={{ siteHref: "/lucentpad/" }}>
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={["/traces?range=7d"]}>
            <AppRoutes />
          </MemoryRouter>
        </QueryClientProvider>
      </DemoModeContext.Provider>,
    );
    const banner = screen.getByRole("note");
    expect(banner).toHaveTextContent("sample data, read-only");
    expect(within(banner).getByRole("link", { name: "Back to LucentPad" })).toHaveAttribute(
      "href",
      "/lucentpad/",
    );
    expect(screen.getByText("Demo data")).toBeInTheDocument();
    expect(screen.queryByText("API connected")).not.toBeInTheDocument();
    const table = await screen.findByRole("table");
    const rows = await within(table).findAllByRole("row");
    expect(rows.length).toBeGreaterThan(10);
    expect(within(table).getAllByText(snapshot.traces[0]!.name).length).toBeGreaterThan(0);
    // The filter panel's counts come from the adapter's facets.
    const panel = screen.getByRole("complementary", { name: "Filters" });
    expect(await within(panel).findByRole("checkbox", { name: "support-agent.run" })).toBeVisible();
  });
});
