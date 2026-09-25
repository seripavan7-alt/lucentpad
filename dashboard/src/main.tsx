import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/tokens.css";
import "./styles/global.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { AppProviders, AppRoutes } from "./App";
import { applyThemePreference, readThemePreference } from "./lib/theme";

// Apply the saved theme before first render to avoid a flash of the wrong theme.
applyThemePreference(readThemePreference());

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing from index.html");

if (import.meta.env.MODE === "site") {
  // Landing site + static demo (GitHub Pages). Compiled out of the normal dashboard build.
  void import("./site/entry").then(({ startSite }) => startSite(root));
} else {
  createRoot(root).render(
    <StrictMode>
      <AppProviders>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </AppProviders>
    </StrictMode>,
  );
}
