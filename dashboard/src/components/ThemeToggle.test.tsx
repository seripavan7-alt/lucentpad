import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { THEME_STORAGE_KEY } from "../lib/theme";
import { mockFetch, renderApp } from "../test/render";
import { traceListPage1 } from "../test/fixtures";

describe("ThemeToggle", () => {
  it("sets data-theme and persists the choice", async () => {
    mockFetch({ "/v1/traces": () => traceListPage1 });
    const user = userEvent.setup();
    renderApp("/costs");
    const root = document.documentElement;
    expect(root).not.toHaveAttribute("data-theme");

    await user.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Dark theme" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Light theme" }));
    expect(root).toHaveAttribute("data-theme", "light");

    await user.click(screen.getByRole("button", { name: "System theme" }));
    expect(root).not.toHaveAttribute("data-theme");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("restores a saved preference", () => {
    mockFetch({});
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    renderApp("/costs");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("falls back to system when storage throws", async () => {
    mockFetch({});
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
    const user = userEvent.setup();
    renderApp("/costs");
    expect(screen.getByRole("button", { name: "System theme" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });
});
