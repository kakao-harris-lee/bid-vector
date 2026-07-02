# Local Dev-Only Roadmap Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for code changes. The controller will coordinate workers with disjoint write scopes; do not edit files outside your assigned ownership.

**Goal:** Advance roadmap-backed work that can be developed on this laptop without operational data collection, production writes, or external notification sends.

**Architecture:** Keep every task fixture-driven and local. Frontend work uses Vitest and split Vite bundle boundaries. Backend helper work uses pytest fixtures and local JSON inputs only. The controller owns integration, final verification, and any roadmap/runbook wording updates.

**Tech Stack:** Python 3, pytest, argparse/json/pathlib, React 19, TanStack Query, React Testing Library, Vitest, Vite split bundles.

---

## Non-Goals

- Do not run KONEPS collection, strategy monitor writes, DB migrations against an operational database, Telegram/app sends, Celery schedules, or production deployment.
- Do not create or depend on real evidence under `reports/g2-evidence/` outside test `tmp_path` fixtures.
- Do not run `scripts/run_g2_synthetic_evidence.py --write` against a real service.
- Do not hand-edit generated `frontend/src/shared/types/openapi.d.ts` or generated `docs/api/*` output unless the controller explicitly provides a generation result.

## Roadmap Mapping

- Phase 2 admin/user split and G-2 surface separation: Worker A.
- Phase 2 notification target verification and masking policy: Worker B.
- Phase 2 blocking gap cleanup and G-2 exit review readiness tooling: Worker C.
- Phase 1 sample-gap execution planning and Phase 2 operator-scoped evidence safety: Worker D.
- Cross-workstream API contract and generated type drift guard: Worker E.

## Work Partition

### Worker A: Split Surface UI Regression

**Owns:**
- Modify if needed: `frontend/src/app/layout/Shell.tsx`
- Modify if needed: `frontend/src/app/router.tsx`
- Modify if needed: `frontend/src/app/router-admin.tsx`
- Test: `frontend/src/app/layout/OperatorSwitcher.test.tsx`
- Test: `frontend/src/shared/crossAppNav.test.tsx`
- Test: `frontend/src/features/operations/OperationsScreen.test.tsx`
- Test: `frontend/src/features/dashboard/HomeScreen.test.tsx`

**Do not edit:** Python scripts, backend APIs, generated OpenAPI types, docs outside this plan.

**Behavior:**
- Add regression coverage that `/admin/operations` in the admin bundle renders only admin navigation and admin context controls.
- Assert the admin home does not show the user bottom navigation or duplicate user-surface menu entries such as `오늘`, `입찰`, `투찰`, `결과` as a separate user menu.
- Preserve valid cross-surface links: admin screens may link to `/dashboard/...` only through full-page navigation helpers.
- Preserve the user dashboard behavior: `/dashboard` must not expose admin-only operations links or cross-operator switcher.

**Steps:**
- [ ] Add a failing regression test for the admin home surface. Use the existing admin route test harness in `frontend/src/app/layout/OperatorSwitcher.test.tsx` or `frontend/src/features/operations/OperationsScreen.test.tsx`.
- [ ] Run:
  ```bash
  npm --prefix frontend test -- OperatorSwitcher.test.tsx OperationsScreen.test.tsx
  ```
  Expected before a needed fix: FAIL only if the admin surface still leaks user menu UI.
- [ ] If the test fails, patch the smallest surface guard in `Shell.tsx`, `router.tsx`, or `router-admin.tsx` so user-only navigation is rendered only when `surface === "user"`.
- [ ] Add or update a cross-app navigation assertion in `frontend/src/shared/crossAppNav.test.tsx` if a route-boundary helper changes.
- [ ] Run:
  ```bash
  npm --prefix frontend test -- OperatorSwitcher.test.tsx OperationsScreen.test.tsx HomeScreen.test.tsx crossAppNav.test.tsx
  npm --prefix frontend run build
  git diff --check -- frontend/src/app frontend/src/features/operations frontend/src/features/dashboard frontend/src/shared
  ```

### Worker B: Notification Target Verifier Fixture Matrix

**Owns:**
- Modify if needed: `scripts/verify_g2_notification_targets.py`
- Test: `tests/test_verify_g2_notification_targets.py`

**Do not edit:** frontend files, G-2 exit builder/checker files, real evidence files.

**Behavior:**
- Strengthen local-only verification around raw target masking, synthetic/non-canonical dry-run policy, operator mismatch, and active Telegram routing.
- Keep the verifier file-only: no HTTP, DB, Telegram, or environment secret reads.
- Keep `--allow-active-noncanonical` as an explicit downgrade from failure to warning, never as a silent pass.

**Steps:**
- [ ] Add parametrized tests covering these fixture cases:
  - raw secret-like values in top-level metadata and channel metadata yield `raw_secret_like_target`;
  - active non-canonical Telegram without `dry_run_only=true` yields `active_noncanonical_telegram` failure by default;
  - the same active non-canonical Telegram fixture becomes a warning with `--allow-active-noncanonical`;
  - empty channels with an explicit skip/dry-run policy pass;
  - operator id or username mismatch is reported as a failure if the current implementation has a matching issue code.
- [ ] Run:
  ```bash
  pytest tests/test_verify_g2_notification_targets.py -q
  ```
  Expected before a needed fix: FAIL only for missing issue detection or unstable severity.
- [ ] If a fixture exposes missing behavior, patch `scripts/verify_g2_notification_targets.py` with a focused helper rather than broad string scanning.
- [ ] Run:
  ```bash
  pytest tests/test_verify_g2_notification_targets.py -q
  python3 -m py_compile scripts/verify_g2_notification_targets.py
  git diff --check -- scripts/verify_g2_notification_targets.py tests/test_verify_g2_notification_targets.py
  ```

### Worker C: G-2 Gap And Exit Readiness Status Consistency

**Owns:**
- Modify if needed: `scripts/g2_blocking_gap_register.py`
- Modify if needed: `scripts/build_g2_exit_review.py`
- Modify if needed: `scripts/check_g2_exit_readiness.py`
- Test: `tests/test_g2_blocking_gap_register.py`
- Test: `tests/test_build_g2_exit_review.py`
- Test: `tests/test_check_g2_exit_readiness.py`

**Do not edit:** notification verifier, sample-gap CLI, frontend files, real evidence files.

**Behavior:**
- Make unresolved gap semantics consistent across register, review builder, and readiness checker.
- Treat `open`, `triaged`, and `accepted_hold` as unresolved for readiness purposes.
- Treat only `resolved` and `excluded` as non-blocking.
- Preserve `accepted_hold` as visible evidence, but prevent it from making a review bundle `ready_for_review`.

**Steps:**
- [ ] Add a failing test in `tests/test_build_g2_exit_review.py` proving a `triaged` or `accepted_hold` gap keeps `manifest.status == "draft"` and `review_gate_summary.ready_for_review == false`.
- [ ] Add or adjust readiness/register tests so the same unresolved statuses produce non-zero open blocking gap counts.
- [ ] Run:
  ```bash
  pytest tests/test_build_g2_exit_review.py tests/test_g2_blocking_gap_register.py tests/test_check_g2_exit_readiness.py -q
  ```
  Expected before a needed fix: FAIL if the review builder only blocks literal `open`.
- [ ] Patch the smallest shared status helper or local constants in the owned scripts. Do not change the manifest schema.
- [ ] Run:
  ```bash
  pytest tests/test_build_g2_exit_review.py tests/test_g2_blocking_gap_register.py tests/test_check_g2_exit_readiness.py -q
  python3 -m py_compile scripts/g2_blocking_gap_register.py scripts/build_g2_exit_review.py scripts/check_g2_exit_readiness.py
  git diff --check -- scripts/g2_blocking_gap_register.py scripts/build_g2_exit_review.py scripts/check_g2_exit_readiness.py tests/test_g2_blocking_gap_register.py tests/test_build_g2_exit_review.py tests/test_check_g2_exit_readiness.py
  ```

### Worker D: Sample-Gap Dry-Run UX Visibility

**Owns:**
- Modify if needed: `frontend/src/features/synthetic-backtest/ExperimentLabScreen.tsx`
- Modify if needed: `frontend/src/features/synthetic-backtest/SampleReportView.tsx`
- Modify if needed: `frontend/src/shared/types/synthetic.ts`
- Modify if needed: `frontend/src/shared/api/synthetic.ts`
- Test: create or modify `frontend/src/features/synthetic-backtest/ExperimentLabScreen.test.tsx`
- Test: `frontend/src/features/synthetic-backtest/SyntheticBacktestScreen.test.tsx`

**Do not edit:** backend synthetic service, sample-gap CLI, G-2 exit scripts, generated OpenAPI types.

**Behavior:**
- Surface sample-gap candidate safety reasons before an operator can select or save an execution path.
- Display `operator_id_scope_ready`, unresolved operator targets, `blocked_by_warnings`, and `run_allowed` in the selected candidate panel.
- Disable execution-selection actions when the candidate is blocked by mixed data or unresolved operator scope.
- Keep the UI fixture-driven; do not call a real backend.

**Steps:**
- [ ] Add a fixture-based test for a sample-gap candidate with `operator_id_scope_ready=false`, one unresolved target, and `run_allowed=true`. Assert the panel shows the unresolved target and an operator-scope warning.
- [ ] Add a fixture-based test for `blocked_by_warnings=["canonical_synthetic_mixed"]`. Assert the panel keeps the destructive warning and does not show an enabled action that implies approval.
- [ ] Run:
  ```bash
  npm --prefix frontend test -- ExperimentLabScreen.test.tsx SyntheticBacktestScreen.test.tsx
  ```
  Expected before a needed fix: FAIL if the candidate panel hides operator-scope readiness.
- [ ] Patch the selected candidate panel in `ExperimentLabScreen.tsx`. Prefer a small rendering helper inside the same file unless the helper is reused elsewhere.
- [ ] Run:
  ```bash
  npm --prefix frontend test -- ExperimentLabScreen.test.tsx SyntheticBacktestScreen.test.tsx
  npm --prefix frontend run build
  git diff --check -- frontend/src/features/synthetic-backtest frontend/src/shared/types/synthetic.ts frontend/src/shared/api/synthetic.ts
  ```

### Worker E: API Contract And Type Sync Discovery

**Owns:**
- Read-only unless the controller approves an edit after the report.
- Inspect: `docs/api/index.md`
- Inspect: `docs/api/operator.md`
- Inspect: `docs/api/analytics.md`
- Inspect: `docs/api/synthetic.md`
- Inspect: `frontend/src/shared/types/openapi.d.ts`
- Inspect: `frontend/package.json`
- Inspect: `tests/test_openapi_tags.py`

**Do not edit:** generated API docs, generated OpenAPI types, application code.

**Behavior:**
- Produce a short report that answers:
  - whether this repo has a checked-in command to regenerate `frontend/src/shared/types/openapi.d.ts`;
  - whether `docs/api/index.md` documents the current generation owner clearly enough;
  - whether there is a local drift check that can run without a server or operational data;
  - the exact minimal follow-up edit, if any, that should be added in a later implementation pass.

**Steps:**
- [ ] Inspect the owned files and search for `openapi-typescript`, `sync-types`, `api-doc-pipeline`, and `/openapi.json`.
- [ ] Do not modify generated files.
- [ ] Return a report with file paths, command candidates, and a recommended next implementation task.

## Controller Integration

**Owns after workers report:**
- Modify if needed: `docs/roadmap.md`
- Modify if needed: `docs/operations/g2-evidence-runbook.md`
- Modify if needed: this plan file

**Steps:**
- [ ] Review every worker summary for file-boundary compliance.
- [ ] Inspect diffs for unrelated changes.
- [ ] Run combined local verification:
  ```bash
  pytest tests/test_verify_g2_notification_targets.py tests/test_g2_blocking_gap_register.py tests/test_build_g2_exit_review.py tests/test_check_g2_exit_readiness.py tests/test_run_g2_synthetic_evidence.py -q
  npm --prefix frontend test -- OperatorSwitcher.test.tsx OperationsScreen.test.tsx HomeScreen.test.tsx crossAppNav.test.tsx ExperimentLabScreen.test.tsx SyntheticBacktestScreen.test.tsx
  npm --prefix frontend run build
  git diff --check
  ```
- [ ] Update roadmap/runbook wording only if implementation changed user-visible local commands or readiness semantics.
- [ ] Commit only after all accepted worker diffs are integrated and verification passes.

## Conflict Avoidance Rules

- Workers A and D may both run frontend tests, but they own different feature directories.
- Worker A owns shell/router surface boundary; Worker D owns synthetic lab candidate visibility.
- Worker B owns only notification verifier files.
- Worker C owns only gap/register/review/readiness files.
- Worker E is read-only discovery.
- Every worker must report status as `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, plus exact files changed and exact commands run.
