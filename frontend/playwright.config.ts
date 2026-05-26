import { defineConfig, devices } from "@playwright/test";

/**
 * Happy-path E2E configuration. Run locally with:
 *
 *   1) Backend up at http://localhost:3000 with `synthetic-operator` 시드된 상태
 *   2) `npm --prefix frontend run dev` (port 3000 with /api proxy)
 *   3) `npx --prefix frontend playwright install --with-deps chromium` (once)
 *   4) `npm --prefix frontend run e2e`
 *
 * CI: not wired by default — see e2e/README.md for the env contract.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "list" : "html",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    headless: true
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ]
});
