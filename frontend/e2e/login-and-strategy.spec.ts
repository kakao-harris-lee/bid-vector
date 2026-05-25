/**
 * Phase 7 happy-path E2E. Run with `npm --prefix frontend run e2e` after
 * bootstrapping chromium via `npm run e2e:install`. See `frontend/e2e/README.md`
 * for the env contract.
 *
 * Intentionally minimal — covers the auth → topbar → strategy save loop.
 */

import { expect, test } from "@playwright/test";

test("로그인 → 전략 편집 → 저장 → 토스트", async ({ page }) => {
  // Vite serves the SPA under `base: "/dashboard/"` — the trailing slash is
  // required, otherwise the dev server returns its "did you mean ..." redirect.
  await page.goto("/dashboard/");

  // Login form
  await page.getByLabel("아이디").fill(process.env.E2E_USERNAME ?? "operator");
  await page.getByLabel("비밀번호").fill(process.env.E2E_PASSWORD ?? "password123");
  await page.getByRole("button", { name: "로그인" }).click();

  // Dashboard shell rendered
  await expect(page.getByRole("heading", { name: "오늘 할 일" })).toBeVisible();

  // Navigate to projects via topbar icon
  await page.getByRole("button", { name: "공고 탐색" }).click();
  await expect(page.getByRole("heading", { name: "공고 탐색" })).toBeVisible();

  // Navigate to strategy editor and save with no changes — should succeed
  await page.getByRole("button", { name: "전략 편집" }).click();
  await expect(page.getByRole("heading", { name: "전략 편집", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "저장" }).click();
  await expect(page.getByText("전략 저장 완료")).toBeVisible();
});
