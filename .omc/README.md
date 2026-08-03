# OMC workspace policy

OMC uses this directory for both durable artifacts and host-local runtime state.
Only reviewed, portable artifacts belong in Git.

## Shared

- `.omc/README.md` and `.omc/.gitignore`
- reviewed specifications under `.omc/specs/`
- stable agent instructions under `.claude/rules/` or `.claude/skills/`

## Local only

- `project-memory.json` and its temporary files: generated data containing the
  checkout's absolute path, scan timestamps, detected commands, and hot paths
- `state/` and `sessions/`: active-mode, tool-error, mission, and agent state
- `notepad.md`, `notepads/`, and `logs/`: session working context
- `plans/`, `interviews/`, and generated artifacts until reviewed and promoted

On another host, including the operating server, OMC should generate fresh
runtime state for that checkout. Promote durable project rules to
`CLAUDE.md`/`.claude/` and durable implementation plans to `docs/`; do not
force-add ignored runtime files.
