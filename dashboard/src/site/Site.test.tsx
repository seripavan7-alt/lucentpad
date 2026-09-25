import { render, screen } from "@testing-library/react";
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
  it("has the two central actions", () => {
    renderSite("/");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/AI agents/);
    const main = screen.getByRole("main");
    expect(
      Array.from(main.querySelectorAll("a")).map((a) => [a.textContent, a.getAttribute("href")]),
    ).toEqual([
      ["How to set up and use", "/docs/getting-started"],
      ["Try the demo", "/demo/traces"],
    ]);
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
    expect(screen.getByRole("link", { name: "Try the demo" })).toBeInTheDocument();
  });
});
