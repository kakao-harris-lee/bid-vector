# Shared development and operations guardrails

This file is committed so Claude Code uses the same project rules on development
and operating-server checkouts. `CLAUDE.md` is the canonical policy; if this
checklist conflicts with it, follow `CLAUDE.md`.

## Repository boundaries

- Backend production code lives under `app/`, tests under `tests/`, and frontend
  code under `frontend/src/`. Do not invent a `src/`-based Python layout.
- Keep FastAPI routes, Celery tasks, and React components thin. Put domain logic
  in services/domain modules and keep I/O at explicit boundaries.
- Use typed Pydantic contracts at HTTP, task, external-response, and persisted
  JSON boundaries. Apply `extra="forbid"` or frozen models where that boundary
  requires it; do not force either policy onto every legacy model.
- Inject stateful or external collaborators at useful seams. Do not require a
  `Protocol` abstraction for pure functions or single-use internal helpers.
- Keep API requests free of model loading and inline ML inference. Route ML work
  through the declared Celery ML queues; production must not enable
  `CELERY_ALLOW_INLINE_ML_TASKS`.

## Operating-server safety

- The operating server may use `ENVIRONMENT=production`. Treat it as a real-data
  verification environment even when it has no external user traffic.
- Get user approval before DB writes, backfills, data cleanup, compose restarts
  that cause downtime, ML promotion, or external notifications.
- Never print or commit `.env` contents, credentials, tokens, private keys, raw
  business registration numbers, or production payloads containing them.
- Prefer read-only diagnostics first. Before a state-changing command, resolve
  the exact container, queue, table, manifest, or file in scope.
- Preserve ML release signature, pgvector dimension, eligibility, and pricing
  guardrails. Do not weaken a gate merely to make a check pass.

## Change workflow

- For non-trivial changes, start from current `origin/main` in an isolated
  worktree and an `agent/<purpose>` branch.
- Keep unrelated dirty or untracked files untouched. Stage only reviewed paths.
- Test success is not code review. Read the final diff, report residual risks,
  and obtain explicit user approval before merging a PR that required fixes.
- Treat generated OpenAPI types and frontend build output according to their
  existing repository workflow; do not hand-edit generated artifacts.

## Verification commands

Use the repository's installed environment and existing scripts. Do not replace
them with `uv` or a global strict profile that this repository does not use.

```bash
source .venv/bin/activate
ruff check <changed-python-paths>
mypy app/
pytest -q <targeted-tests>
python scripts/design_ratchet.py
npm --prefix frontend test
npm --prefix frontend run build
python scripts/sync_openapi_types.py --check
```

- Run the serial full backend suite for broad or high-risk changes.
- Run both base and server-overlay Compose config checks when task routing,
  workers, environment settings, or deployment files change.
- Never hide failures with skips, relaxed baselines, disabled signatures, or
  snapshot updates unless the behavioral change is explicitly reviewed.
