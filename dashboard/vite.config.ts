/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { copyFile } from "node:fs/promises";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

/** GitHub Pages serves 404.html for unknown paths; make it the app so deep links work. */
function spaFallback(): Plugin {
  let outDir = "";
  return {
    name: "spa-fallback",
    apply: "build",
    enforce: "post",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    async writeBundle() {
      await copyFile(resolve(outDir, "index.html"), resolve(outDir, "404.html"));
    },
  };
}

const apiTarget = process.env.LUCENTPAD_API_URL ?? "http://localhost:8000";

// `vite build --mode site`: landing site + static demo for GitHub Pages, served under /lucentpad/.
export default defineConfig(({ mode }) => ({
  base: mode === "site" ? (process.env.LUCENTPAD_SITE_BASE ?? "/lucentpad/") : "/",
  // The demo snapshot is one ~1 MB data chunk (≈100 kB gzipped), loaded only by /demo.
  build: mode === "site" ? { outDir: "dist-site", chunkSizeWarningLimit: 1200 } : {},
  plugins: [react(), ...(mode === "site" ? [spaFallback()] : [])],
  server: {
    port: 5173,
    strictPort: true,
    host: true,
    // The landing site renders ../docs/getting-started.md.
    fs: { allow: [".", "../docs"] },
    proxy: {
      "/v1": { target: apiTarget, changeOrigin: true },
      "/healthz": { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
}));
