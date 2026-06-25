# G-2 Exit Review Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for code changes. The controller will coordinate multiple workers in parallel with disjoint write scopes; do not edit files outside your assigned ownership.

**Goal:** Build the next G-2 execution helpers needed before Phase 3: richer read-only evidence capture, a local exit-review bundle builder, and a safer sample-gap write preflight.

**Architecture:** Keep every operation local/read-only by default. The HTTP collector only performs GET requests and writes JSON files under the selected evidence directory. The review builder consumes existing local manifests and writes a draft review bundle. The sample-gap CLI blocks approved writes when the candidate cannot resolve synthetic slugs to active operator IDs.

**Tech Stack:** Python 3, argparse, pathlib/json, existing FastAPI endpoint contracts, pytest, existing React/Vitest only for already covered guide work.

---

## Work Partition

### Worker A: Expanded HTTP Evidence Collector

**Owns:**
- Modify: `scripts/collect_g2_evidence.py`
- Modify: `tests/test_g2_evidence_collection.py`

**Do not edit:** `scripts/run_g2_synthetic_evidence.py`, `scripts/build_g2_exit_review.py`, docs, frontend.

**Behavior:**
- Extend the collector's read-only endpoint set beyond profile/strategy/notification/G-2 ledger.
- Add GET-only snapshots for:
  - `operator_dashboard`: `/api/v1/operator/dashboard?operator_id=<id>&days=<days>&limit=5`
  - `operations_dashboard`: `/api/v1/analytics/operations-dashboard?operator_id=<id>&days=<days>&recent_limit=5`
  - `strategy_candidates`: `/api/v1/operator/strategy/candidates?operator_id=<id>&limit=20&high_priority_only=true`
  - `decision_experiments`: `/api/v1/analytics/decision-experiments?operator_id=<id>&limit=20&sort=needs_attention`
  - `decision_recommendations`: `/api/v1/analytics/decision-recommendations?operator_id=<id>&days=<days>&recommendation_limit=5`
- Preserve the existing invariant: no POST, no DB write, no KONEPS direct call, no Telegram send.
- Map these new raw paths into `manifest-draft.json`:
  - `strategy_candidates.json` -> `evidence_paths.candidate_preview`
  - `operator-dashboard.json` and `operations-dashboard.json` -> `evidence_paths.operations_dashboard`
  - `decision-experiments.json` -> `evidence_paths.decision_experiments`
  - `decision-recommendations.json` may remain in raw files and daily details; it is supporting evidence, not a template path by itself.
- Do not mark daily status pass just because the new optional files exist. Pass still requires G-2 ledger `ready`, operator scope pass, profile pass, strategy pass, notification pass, zero blocking gaps, and zero collection errors.

**TDD steps:**
- [ ] Add a failing test in `tests/test_g2_evidence_collection.py` named `test_collection_writes_extended_read_only_evidence_files`.
  - It should assert the fake HTTP client receives the five new GET paths.
  - It should assert the run directory contains `operator-dashboard.json`, `operations-dashboard.json`, `strategy-candidates.json`, `decision-experiments.json`, and `decision-recommendations.json`.
  - It should assert params include `operator_id`, `days` where applicable, `limit`/`recent_limit` where applicable, and `high_priority_only` for strategy candidates.
- [ ] Run:
  ```bash
  pytest tests/test_g2_evidence_collection.py::test_collection_writes_extended_read_only_evidence_files -q
  ```
  Expected before implementation: FAIL because paths/files are missing.
- [ ] Implement endpoint metadata in `scripts/collect_g2_evidence.py`. Prefer extending `EndpointSpec.params()` with optional `days`, `limit`, `recent_limit`, `sort`, and static params rather than adding ad hoc branches in the collector loop.
- [ ] Add a failing assertion or second test named `test_manifest_links_extended_evidence_paths`.
  - It should assert manifest operator evidence paths include candidate preview, operations dashboard, and decision experiments.
  - It should assert `decision_apply_dry_run` stays empty.
- [ ] Run the focused tests again and make them pass:
  ```bash
  pytest tests/test_g2_evidence_collection.py -q
  ```
- [ ] Run owned-file checks:
  ```bash
  python3 -m py_compile scripts/collect_g2_evidence.py
  git diff --check -- scripts/collect_g2_evidence.py tests/test_g2_evidence_collection.py
  ```

### Worker B: Local G-2 Exit Review Bundle Builder

**Owns:**
- Create: `scripts/build_g2_exit_review.py`
- Create: `tests/test_build_g2_exit_review.py`

**Do not edit:** `scripts/collect_g2_evidence.py`, `scripts/run_g2_synthetic_evidence.py`, docs, frontend.

**Behavior:**
- Add a local-only CLI that reads existing `manifest-draft.json` files and writes a review bundle:
  - `reports/g2-evidence/<review_id>/manifest.json`
  - `reports/g2-evidence/<review_id>/exit-review.md`
- CLI options:
  - `--evidence-root <path>` required
  - `--review-id <id>` required
  - `--output-dir <path>` optional, default `<evidence-root>/<review_id>`
  - `--min-days <n>` optional default `7`
  - `--min-operators <n>` optional default `3`
- It must not read the database, call HTTP, run monitors, enqueue tasks, or send notifications.
- It should find `manifest-draft.json` recursively under `evidence-root`, excluding the output directory if it already exists.
- It should merge:
  - `basis` from the newest draft, preserving doc paths and basis commit
  - unique operators by `operator_id`
  - all `daily_status` rows sorted by date
  - all `blocking_gaps`, preserving open/resolved/excluded status
  - `action_register.dry_run_items` and `approved_execution_items`
- It should set top-level status:
  - `ready_for_review` only when counted/pass days >= `min_days`, included operators >= `min_operators`, and no `blocking_gaps` with `status=open`
  - otherwise `draft`
- It should include `review_gate_summary` with `counted_days`, `required_days`, `operator_count`, `required_operator_count`, `open_blocking_gap_count`, and `ready_for_review`.
- `exit-review.md` should be a generated draft, not an approval. It should include final line `G-2 exit: pending`.

**TDD steps:**
- [ ] Add `tests/test_build_g2_exit_review.py::test_build_review_bundle_combines_daily_manifest_drafts`.
  - Create two fake daily directories with `manifest-draft.json`.
  - One operator set should overlap; assert unique operators.
  - Assert `manifest.json` and `exit-review.md` are written.
  - Assert daily rows are sorted and status is `ready_for_review` when thresholds pass.
- [ ] Run:
  ```bash
  pytest tests/test_build_g2_exit_review.py::test_build_review_bundle_combines_daily_manifest_drafts -q
  ```
  Expected before implementation: FAIL because script/module is missing.
- [ ] Implement pure functions:
  - `load_manifest_drafts(evidence_root: Path, output_dir: Path) -> list[dict[str, Any]]`
  - `build_review_manifest(drafts: list[dict[str, Any]], review_id: str, min_days: int, min_operators: int) -> dict[str, Any]`
  - `render_exit_review(manifest: dict[str, Any]) -> str`
  - `write_review_bundle(...) -> dict[str, Path]`
- [ ] Add `tests/test_build_g2_exit_review.py::test_review_bundle_stays_draft_with_open_gaps`.
  - Include one `blocking_gaps` item with `status=open`.
  - Assert top-level status `draft`, `ready_for_review=false`, and markdown says pending.
- [ ] Add `tests/test_build_g2_exit_review.py::test_cli_rejects_when_no_manifest_drafts`.
  - Call `main([...])` with an empty evidence root.
  - Assert exit code `2` and no output files.
- [ ] Run:
  ```bash
  pytest tests/test_build_g2_exit_review.py -q
  python3 -m py_compile scripts/build_g2_exit_review.py
  git diff --check -- scripts/build_g2_exit_review.py tests/test_build_g2_exit_review.py
  ```

### Worker C: Sample-Gap Operator-Scope Write Guard

**Owns:**
- Modify: `scripts/run_g2_synthetic_evidence.py`
- Create: `tests/test_run_g2_synthetic_evidence.py`

**Do not edit:** `scripts/collect_g2_evidence.py`, `scripts/build_g2_exit_review.py`, docs, frontend.

**Behavior:**
- Make `scripts/run_g2_synthetic_evidence.py` testable by allowing `main(argv=None, session_factory=SessionLocal, service_factory=SyntheticExperimentService)`.
- Preserve default dry-run behavior and existing output structure.
- In dry-run output, include an explicit top-level `operator_scope` summary copied from the candidate:
  - `operator_id_scope_ready`
  - `operator_targets`
  - `unresolved_operator_targets`
- In `--write` mode, build the candidate first. If `operator_id_scope_ready` is false, do not call `materialize_sample_gap_candidate_run`; print status `blocked_operator_scope`, `write_performed=false`, and return exit code `4`.
- Preserve existing mixed-data block handling with exit code `3`.

**TDD steps:**
- [ ] Add `tests/test_run_g2_synthetic_evidence.py::test_dry_run_outputs_operator_scope_summary`.
  - Patch the session/service factory with a fake service.
  - Fake candidate should include one resolved and one unresolved target.
  - Assert exit code `0` and output JSON has `operator_scope.operator_id_scope_ready=false`.
- [ ] Run:
  ```bash
  pytest tests/test_run_g2_synthetic_evidence.py::test_dry_run_outputs_operator_scope_summary -q
  ```
  Expected before implementation: FAIL because `main()` does not accept injectable argv/factories and output lacks top-level `operator_scope`.
- [ ] Implement injectable `main(...)`, `operator_scope_summary(candidate)`, and keep the command-line `if __name__ == "__main__"` behavior.
- [ ] Add `tests/test_run_g2_synthetic_evidence.py::test_write_blocks_when_operator_scope_not_ready`.
  - Fake service should count calls to `materialize_sample_gap_candidate_run`.
  - Assert exit code `4`, JSON status `blocked_operator_scope`, and materialize count `0`.
- [ ] Add `tests/test_run_g2_synthetic_evidence.py::test_write_materializes_when_operator_scope_ready`.
  - Fake service returns candidate ready and materialize result queued.
  - Assert exit code `0`, JSON status `queued`, and materialize count `1`.
- [ ] Run:
  ```bash
  pytest tests/test_run_g2_synthetic_evidence.py -q
  python3 -m py_compile scripts/run_g2_synthetic_evidence.py
  git diff --check -- scripts/run_g2_synthetic_evidence.py tests/test_run_g2_synthetic_evidence.py
  ```

### Controller Integration Task: Docs, Review, Full Verification

**Owns:**
- Modify after workers finish: `docs/operations/g2-evidence-runbook.md`
- Modify after workers finish: `docs/api/synthetic.md`
- Optionally modify: `README.md` only if commands change user-facing workflow.

**Steps:**
- [ ] Review Worker A/B/C diffs for file-boundary compliance.
- [ ] Update runbook commands:
  - mention new extended collector raw files
  - add `scripts/build_g2_exit_review.py` command
  - document `blocked_operator_scope` and exit code `4`
- [ ] Update synthetic API CLI section with `operator_scope` dry-run summary and write guard.
- [ ] Run focused suites:
  ```bash
  pytest tests/test_g2_evidence_collection.py tests/test_build_g2_exit_review.py tests/test_run_g2_synthetic_evidence.py tests/test_collect_g2_evidence.py tests/test_synthetic_experiment.py tests/test_synthetic_experiment_operator_scope.py -q
  python3 -m py_compile scripts/collect_g2_evidence.py scripts/build_g2_exit_review.py scripts/run_g2_synthetic_evidence.py app/services/synthetic_experiment.py app/schemas/schemas.py
  git diff --check
  ```
- [ ] Run frontend build only if docs/frontend files changed unexpectedly:
  ```bash
  npm --prefix frontend run build
  ```
- [ ] Commit all integrated work:
  ```bash
  git add scripts tests docs README.md
  git commit -m "Add G-2 exit review bundle tooling"
  ```

## Conflict Avoidance Rules

- Worker A edits only collector script and collector tests.
- Worker B creates only the review builder script and its tests.
- Worker C edits only sample-gap CLI and creates its tests.
- No worker edits docs. Controller updates docs after code integration.
- No worker runs DB write paths, KONEPS calls, Telegram sends, or production deployment commands.
- Each worker must report status as `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, with exact files changed and tests run.
