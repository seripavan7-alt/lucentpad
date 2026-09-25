import userEvent from "@testing-library/user-event";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { SiteRoutes } from "./SiteApp";

function renderSite(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <SiteRoutes />
    </MemoryRouter>,
  );
}

describe("Landing site", () => {
  it("has the two central actions: open the dashboard, get started", () => {
    renderSite("/");
    expect(
      screen.getByRole("heading", { level: 1, name: "See what your agent actually did." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the dashboard" })).toHaveAttribute(
      "href",
      "/demo/traces",
    );
    expect(screen.getByRole("link", { name: /Get started/ })).toHaveAttribute(
      "href",
      "#get-started",
    );
  });

  it("embeds the real dashboard and shows setup on the page, full guide on click", async () => {
    const user = userEvent.setup();
    renderSite("/");
    expect(screen.getByTitle("LucentPad dashboard with sample data")).toHaveAttribute(
      "src",
      "/demo/traces",
    );
    expect(screen.getByRole("tab", { name: "Run it locally" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const panel = () => within(screen.getByRole("tabpanel"));
    expect(panel().getByText(/git clone https:\/\/github.com/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Trace your agent" }));
    expect(panel().getByText(/lucentpad\.wrap\(Anthropic\(\)\)/)).toBeInTheDocument();
    const guide = screen.getByText("Full setup guide").closest("details")!;
    expect(guide).not.toHaveAttribute("open");
    await user.click(screen.getByText("Full setup guide"));
    expect(guide).toHaveAttribute("open");
  });

  it("renders the getting-started guide from docs/getting-started.md", () => {
    renderSite("/docs/getting-started");
    expect(
      screen.getByRole("heading", { level: 1, name: "Getting started with LucentPad" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "1. Run it locally" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/git clone/, { selector: "pre code" })).toBeInTheDocument();
  });

  it("sends unknown paths back to the landing page", () => {
    renderSite("/nope");
    expect(screen.getByRole("link", { name: "Open the dashboard" })).toBeInTheDocument();
  });
});
