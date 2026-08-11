#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROMPT_FILE="${REPO_ROOT}/.claude/review/kotlin-codex-review.md"
SCHEMA_FILE="${REPO_ROOT}/.claude/review/kotlin-codex-review.schema.json"

scope_mode="base"
scope_value="origin/main"
output_path=""
dry_run="false"

usage() {
  printf '%s\n' \
    "Usage: scripts/codex-review-kotlin.sh [scope] [options]" \
    "" \
    "Scopes (choose one; default: --base origin/main):" \
    "  --base <branch>       Review current branch against base" \
    "  --commit <sha>        Review one commit" \
    "  --uncommitted         Review staged, unstaged, and untracked changes" \
    "" \
    "Options:" \
    "  --output <path>       JSON report path (default: reports/codex-review/...)" \
    "  --dry-run             Validate and print the resolved invocation only" \
    "  -h, --help            Show this help"
}

scope_seen="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      [[ "${scope_seen}" == "false" ]] || { printf 'Only one review scope is allowed.\n' >&2; exit 64; }
      [[ $# -ge 2 ]] || { printf '%s\n' '--base requires a branch.' >&2; exit 64; }
      scope_mode="base"
      scope_value="$2"
      scope_seen="true"
      shift 2
      ;;
    --commit)
      [[ "${scope_seen}" == "false" ]] || { printf 'Only one review scope is allowed.\n' >&2; exit 64; }
      [[ $# -ge 2 ]] || { printf '%s\n' '--commit requires a SHA.' >&2; exit 64; }
      scope_mode="commit"
      scope_value="$2"
      scope_seen="true"
      shift 2
      ;;
    --uncommitted)
      [[ "${scope_seen}" == "false" ]] || { printf 'Only one review scope is allowed.\n' >&2; exit 64; }
      scope_mode="uncommitted"
      scope_value=""
      scope_seen="true"
      shift
      ;;
    --output)
      [[ $# -ge 2 ]] || { printf '%s\n' '--output requires a path.' >&2; exit 64; }
      output_path="$2"
      shift 2
      ;;
    --dry-run)
      dry_run="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

command -v git >/dev/null 2>&1 || { printf 'git is required.\n' >&2; exit 69; }
command -v codex >/dev/null 2>&1 || { printf 'Codex CLI is required.\n' >&2; exit 69; }
git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null
[[ -f "${PROMPT_FILE}" ]] || { printf 'Missing prompt: %s\n' "${PROMPT_FILE}" >&2; exit 66; }
[[ -f "${SCHEMA_FILE}" ]] || { printf 'Missing schema: %s\n' "${SCHEMA_FILE}" >&2; exit 66; }

current_branch="$(git -C "${REPO_ROOT}" branch --show-current)"

case "${scope_mode}" in
  base|commit)
    git -C "${REPO_ROOT}" rev-parse --verify "${scope_value}^{commit}" >/dev/null
    ;;
  uncommitted)
    if [[ "${current_branch}" == "main" || "${current_branch}" == "master" ]]; then
      printf '%s\n' 'Refusing --uncommitted review on main/master; use an isolated feature worktree.' >&2
      exit 65
    fi
    if [[ -z "$(git -C "${REPO_ROOT}" status --porcelain)" ]]; then
      printf 'No uncommitted changes to review.\n' >&2
      exit 65
    fi
    ;;
esac

if [[ -z "${output_path}" ]]; then
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  output_path="${REPO_ROOT}/reports/codex-review/kotlin-${timestamp}.json"
elif [[ "${output_path}" != /* ]]; then
  output_path="${REPO_ROOT}/${output_path}"
fi

if [[ -e "${output_path}" ]]; then
  printf 'Refusing to overwrite existing report: %s\n' "${output_path}" >&2
  exit 73
fi

review_args=(exec review --ephemeral --output-schema "${SCHEMA_FILE}" --output-last-message "${output_path}")
case "${scope_mode}" in
  base)
    review_args+=(--base "${scope_value}")
    ;;
  commit)
    review_args+=(--commit "${scope_value}")
    ;;
  uncommitted)
    review_args+=(--uncommitted)
    ;;
esac
review_args+=(-)

printf 'Codex Kotlin review scope: %s%s\n' \
  "${scope_mode}" "$([[ -n "${scope_value}" ]] && printf '=%s' "${scope_value}")"
printf 'Report: %s\n' "${output_path}"

if [[ "${dry_run}" == "true" ]]; then
  printf 'Command:'
  printf ' %q' codex "${review_args[@]}"
  printf ' < %q\n' "${PROMPT_FILE}"
  exit 0
fi

if [[ "${scope_mode}" == "base" ]] && git -C "${REPO_ROOT}" diff --quiet "${scope_value}...HEAD"; then
  printf 'No committed branch changes against %s. Commit the Kotlin slice or use --uncommitted in an isolated worktree.\n' "${scope_value}" >&2
  exit 65
fi

mkdir -p "$(dirname "${output_path}")"
(
  cd "${REPO_ROOT}"
  codex "${review_args[@]}" < "${PROMPT_FILE}"
)

if grep -Eq '"verdict"[[:space:]]*:[[:space:]]*"request_changes"' "${output_path}"; then
  printf 'Codex verdict: request_changes\n'
  printf 'Read findings: %s\n' "${output_path}"
  exit 2
fi

if grep -Eq '"verdict"[[:space:]]*:[[:space:]]*"approve"' "${output_path}"; then
  printf 'Codex verdict: approve\n'
  exit 0
fi

printf 'Codex report has no recognized verdict: %s\n' "${output_path}" >&2
exit 65
