import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import fs from "node:fs";
import { defineConfig, type Plugin } from "vitest/config";

/**
 * Dual-bundle build (Option A: two entry points, one codebase).
 *
 * `BUILD_TARGET=user`  -> base /dashboard/, outDir dist/dashboard, entry index.html
 * `BUILD_TARGET=admin` -> base /admin/,     outDir dist/admin,     entry admin.html
 *
 * The backend serves each bundle's `index.html`, so the admin build (whose
 * entry html is `admin.html`) is renamed to `index.html` after the build via
 * the `renameAdminEntryHtml` plugin below. This keeps both apps reachable at
 * `dist/<app>/index.html` with the correct asset base baked in.
 */
type BuildTarget = "user" | "admin";

const buildTarget: BuildTarget =
  process.env.BUILD_TARGET === "admin" ? "admin" : "user";

const targets: Record<
  BuildTarget,
  { base: string; outDir: string; entry: string }
> = {
  user: { base: "/dashboard/", outDir: "dist/dashboard", entry: "index.html" },
  admin: { base: "/admin/", outDir: "dist/admin", entry: "admin.html" }
};

const active = targets[buildTarget];

/**
 * The admin entry html is authored as `admin.html` so Vite can use it as a
 * distinct rollup input. The backend serves `dist/admin/index.html`, so after
 * the admin build we rename the emitted `admin.html` to `index.html`.
 */
function renameAdminEntryHtml(outDir: string): Plugin {
  return {
    name: "rename-admin-entry-html",
    apply: "build",
    closeBundle() {
      if (buildTarget !== "admin") return;
      const from = path.resolve(__dirname, outDir, "admin.html");
      const to = path.resolve(__dirname, outDir, "index.html");
      if (fs.existsSync(from)) {
        fs.rmSync(to, { force: true });
        fs.renameSync(from, to);
      }
    }
  };
}

export default defineConfig({
  base: active.base,
  plugins: [react(), tailwindcss(), renameAdminEntryHtml(active.outDir)],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src")
    }
  },
  build: {
    outDir: active.outDir,
    // Each target writes to its own outDir; emptying is safe and keeps stale
    // chunks from a previous target out of the bundle.
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(__dirname, active.entry)
    }
  },
  server: {
    port: 3001,
    proxy: {
      "/api": "http://localhost:3000"
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    globals: true,
    // Playwright specs live under frontend/e2e/ and require browser context.
    // Vitest must not try to collect them.
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"]
  }
});
