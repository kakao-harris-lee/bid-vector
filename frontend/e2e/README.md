# Playwright happy-path E2E (skeleton)

This directory carries the **scaffold** for the Phase 7 happy-path E2E. The
actual `@playwright/test` package is intentionally **not** added to
`devDependencies` yet — installing it pulls down browser binaries on first
run, and we want that decision (and the storage hit) to be explicit.

## Required environment

- Backend `uvicorn app.main:app --port 8000` with seeded synthetic operators
  and at least one stored bid decision / paper bid for the operator account.
- Frontend dev server `npm --prefix frontend run dev` on port 3000 (Vite
  proxies `/api/*` to `http://localhost:8000`).
- Bootstrap test user (default `operator` / `password123`) — see
  `tests/conftest.py` for the canonical credentials.

## Install (one-time)

```bash
npm --prefix frontend install --save-dev @playwright/test
npx --prefix frontend playwright install --with-deps chromium
```

## Run

```bash
npm --prefix frontend run e2e
```

The `e2e` script is intentionally **not** wired into `package.json` until the
above install completes. Add it manually when you onboard a contributor:

```json
"e2e": "playwright test",
"e2e:ui": "playwright test --ui"
```

## What this covers

`login-and-strategy.spec.ts` is a single happy path:

1. Open `/dashboard`, log in as `operator`.
2. Navigate via topbar to **공고 탐색** and confirm a project row loads.
3. Open the **전략 편집** screen, change `minimum_match_score`, save.
4. Confirm "전략 저장 완료" toast appears.

Failures here are typically a Phase regression (router/auth/form). CI
integration is out of scope for the initial scaffold — wire it up once the
Playwright deps are committed.
