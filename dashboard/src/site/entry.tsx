import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { DEMO_PATH } from "./links";
import { SiteRoutes } from "./SiteApp";

/** Site build entry: the landing site, or the static demo under <base>demo/. */
export async function startSite(root: HTMLElement): Promise<void> {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  const demoBase = `${base}/${DEMO_PATH}`;
  const path = window.location.pathname;
  if (path === demoBase || path.startsWith(`${demoBase}/`)) {
    const { startDemo } = await import("../demo/start");
    await startDemo(root, { basename: demoBase, siteHref: `${base}/` });
    return;
  }
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter basename={base || "/"}>
        <SiteRoutes />
      </BrowserRouter>
    </StrictMode>,
  );
}
