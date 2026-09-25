import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { setFetcher } from "../api/client";
import { AppProviders, AppRoutes } from "../App";
import { createLiveDemoFetch, type DemoSnapshot } from "./adapter";
import { DemoModeContext } from "./context";

/** Boot the dashboard over the in-browser sample data. Loaded only by the site build. */
export async function startDemo(
  root: HTMLElement,
  { basename, siteHref }: { basename: string; siteHref: string },
): Promise<void> {
  const raw = (await import("./snapshot.json")).default as unknown as DemoSnapshot;
  // Re-anchored to the viewer's clock on every request (see createLiveDemoFetch).
  setFetcher(createLiveDemoFetch(raw));
  createRoot(root).render(
    <StrictMode>
      <DemoModeContext.Provider value={{ siteHref }}>
        <AppProviders>
          <BrowserRouter basename={basename}>
            <AppRoutes />
          </BrowserRouter>
        </AppProviders>
      </DemoModeContext.Provider>
    </StrictMode>,
  );
}
