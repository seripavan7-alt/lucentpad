/** Where the landing site's links go. BASE_URL is "/lucentpad/" on GitHub Pages, "/" elsewhere. */
export const GITHUB_URL = "https://github.com/seripavan7-alt/lucentpad";

export function siteHref(path = ""): string {
  return `${import.meta.env.BASE_URL}${path}`;
}

/** The static demo is its own app (separate router), so links to it are full page loads. */
export const DEMO_PATH = "demo";
export const DEMO_START = `${DEMO_PATH}/traces`;
