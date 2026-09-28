import userEvent from "@testing-library/user-event";
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { traceListPage1 } from "../test/fixtures";
import { getLocation, mockFetch, renderApp } from "../test/render";

describe("Shell", () => {
  it.each([
    ["/costs", "Costs"],
    ["/guardrails", "Guardrails"],
    ["/evals", "Evals"],
  ])("%s renders its page with a plain nav item (no milestone tag)", (route, title) => {
    mockFetch({});
    renderApp(route);
    expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    const link = within(nav).getByRole("link", { name: new RegExp(title) });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(link).toHaveTextContent(new RegExp(`^${title}$`));
  });

  it("redirects / to /traces", async () => {
    mockFetch({ "/v1/traces": () => traceListPage1 });
    renderApp("/");
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(getLocation()).toBe("/traces");
  });

  it("links the logo home (/ goes to Traces)", async () => {
    mockFetch({ "/v1/traces": () => traceListPage1 });
    const user = userEvent.setup();
    renderApp("/costs");
    await user.click(screen.getByRole("link", { name: "LucentPad home" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(getLocation()).toBe("/traces");
  });

  it("renders a 404 for unknown routes", () => {
    mockFetch({});
    renderApp("/nope");
    expect(screen.getByText("This page doesn't exist")).toBeInTheDocument();
  });

  it("shows API health in the sidebar", async () => {
    mockFetch({});
    renderApp("/costs");
    expect(await screen.findByText("API connected")).toBeInTheDocument();
  });
});
