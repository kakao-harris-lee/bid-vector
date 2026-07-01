# Playwright happy-path E2E

This directory carries the Phase 7 happy-path E2E. The `@playwright/test`
runner is now in `devDependencies`. The browser binary (chromium) is **not**
pulled by `npm install` — run the install script once per workstation.

## Required environment

- Backend `uvicorn app.main:app --port 3000` (or `make dev`) with the
  singleton operator account bootstrapped (`POST /api/v1/auth/bootstrap`) and
  the password matching `E2E_PASSWORD` below.
- Frontend dev server `npm --prefix frontend run dev` on port 3001 (Vite
  proxies `/api/*` to `http://localhost:3000`).

## One-time setup

```bash
npm --prefix frontend run e2e:install   # chromium + system deps
```

If the operator password is unknown (e.g., fresh DB), reset it directly:

```bash
source .venv/bin/activate && python -c "
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.models import User
db = SessionLocal()
u = db.query(User).filter(User.username == 'operator').first()
u.hashed_password = get_password_hash('password123')
db.commit()
"
```

## Run

```bash
npm --prefix frontend run e2e          # headless
npm --prefix frontend run e2e:ui       # UI mode (interactive)
```

Override credentials / base URL via env if your stack differs:

```bash
E2E_USERNAME=operator E2E_PASSWORD=password123 \
E2E_BASE_URL=http://localhost:3000 \
npm --prefix frontend run e2e
```

## What this covers

`login-and-strategy.spec.ts` — single happy path:

1. Open `/dashboard/` (Vite serves the SPA under `base: "/dashboard/"`, so the
   trailing slash matters).
2. Log in as `operator`.
3. Navigate via topbar to **공고 탐색**, confirm the screen renders.
4. Navigate to **전략 편집**, click save (no-op edit), confirm
   "전략 저장 완료" toast.

Failures here are typically a Phase regression (router/auth/form). CI
integration is intentionally out of scope for the scaffold — wire up GH
Actions later by reusing `npm --prefix frontend run e2e` in a job that starts
the backend + frontend in a sidecar.
