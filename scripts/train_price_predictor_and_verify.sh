#!/usr/bin/env bash
set -euo pipefail

# One-shot runner:
# 1) enqueue price-predictor training
# 2) poll task status until completion/failure/timeout
# 3) verify expected artifacts exist and contain valid JSON

BASE_URL="${BASE_URL:-http://localhost:3000}"
CATEGORY="${CATEGORY:-construction}"
LIMIT="${LIMIT:-500}"
NOTES="${NOTES:-construction data training}"
CREATE_MANIFEST="${CREATE_MANIFEST:-true}"
PUBLISH_REMOTE="${PUBLISH_REMOTE:-false}"
RELEASE_TAG="${RELEASE_TAG:-$(date +%F)-price-v1}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-5}"
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-900}"
ALLOW_MEMORY_BROKER="${ALLOW_MEMORY_BROKER:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if ! command -v curl >/dev/null 2>&1; then
  echo "[error] curl is required" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "[error] python3 is required" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "[error] docker is required" >&2
  exit 1
fi

START_RESPONSE_FILE="$(mktemp)"
STATUS_RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$START_RESPONSE_FILE" "$STATUS_RESPONSE_FILE"' EXIT

# Validate API task runtime mode so queued ML jobs can actually execute.
OPS_HTTP_CODE=$(curl -sS -o "$STATUS_RESPONSE_FILE" -w "%{http_code}" \
  "$BASE_URL/api/v1/analytics/operations-dashboard?days=1&recent_limit=1")

if [[ "$OPS_HTTP_CODE" -ge 200 && "$OPS_HTTP_CODE" -lt 300 ]]; then
  PRECHECK_RESULT=$(python3 - "$STATUS_RESPONSE_FILE" "$ALLOW_MEMORY_BROKER" <<'PY'
import json, sys

payload_path = sys.argv[1]
allow_memory = str(sys.argv[2]).strip().lower() == 'true'

with open(payload_path, encoding='utf-8') as f:
    payload = json.load(f)

tasks = payload.get('tasks') if isinstance(payload, dict) else {}
broker = tasks.get('broker') if isinstance(tasks, dict) else {}
runtime = tasks.get('runtime') if isinstance(tasks, dict) else {}
result_backend = tasks.get('result_backend') if isinstance(tasks, dict) else {}

broker_transport = str(broker.get('transport') or '').strip().lower()
backend_transport = str(result_backend.get('transport') or '').strip().lower()
eager_mode = bool(runtime.get('eager_mode'))

errors = []
if not allow_memory:
    if eager_mode:
        errors.append('API runtime is in eager_mode=true (memory broker)')
    if broker_transport in {'memory', 'memory+cache', 'memory://'}:
        errors.append(f'broker transport is {broker_transport}')
    if backend_transport in {'cache+memory', 'memory', 'memory://'}:
        errors.append(f'result backend transport is {backend_transport}')

if errors:
    print('ERROR\t' + '; '.join(errors))
else:
    print('OK\t' + f'broker={broker_transport or "unknown"}, backend={backend_transport or "unknown"}, eager={eager_mode}')
PY
)

  PRECHECK_STATUS="${PRECHECK_RESULT%%$'\t'*}"
  PRECHECK_DETAIL="${PRECHECK_RESULT#*$'\t'}"
  if [[ "$PRECHECK_STATUS" == "ERROR" ]]; then
    echo "[error] ML queue precheck failed: $PRECHECK_DETAIL" >&2
    echo "[hint] API process is likely running with memory:// broker, so training tasks remain queued." >&2
    echo "[hint] restart API with broker-backed settings (e.g. make docker-up-tasks and use the containerized API)." >&2
    echo "[hint] if you intentionally want memory mode, set ALLOW_MEMORY_BROKER=true." >&2
    exit 1
  fi
  echo "[precheck] $PRECHECK_DETAIL"
else
  echo "[warn] operations dashboard precheck unavailable (http=$OPS_HTTP_CODE). continuing without broker sanity check." >&2
fi

# Ensure training worker is running to avoid tasks stuck in queue-only mode.
if ! docker compose ps training-worker | grep -Eq 'training-worker'; then
  echo "[error] training-worker service is not available in compose output." >&2
  echo "        start it first: make docker-up-tasks" >&2
  exit 1
fi
if ! docker compose ps training-worker | grep -Eq 'Up|running|healthy'; then
  echo "[error] training-worker is not running." >&2
  echo "        start it first: make docker-up-tasks" >&2
  exit 1
fi

printf "[train] base_url=%s release_tag=%s category=%s limit=%s create_manifest=%s publish_remote=%s\n" \
  "$BASE_URL" "$RELEASE_TAG" "$CATEGORY" "$LIMIT" "$CREATE_MANIFEST" "$PUBLISH_REMOTE"

START_HTTP_CODE=$(curl -sS -o "$START_RESPONSE_FILE" -w "%{http_code}" \
  -X POST "$BASE_URL/api/v1/ml/training/price-predictor" \
  -H "Content-Type: application/json" \
  -d "{
    \"release_tag\": \"$RELEASE_TAG\",
    \"category\": \"$CATEGORY\",
    \"limit\": $LIMIT,
    \"notes\": \"$NOTES\",
    \"create_manifest\": $CREATE_MANIFEST,
    \"publish_remote\": $PUBLISH_REMOTE
  }")

if [[ "$START_HTTP_CODE" -lt 200 || "$START_HTTP_CODE" -ge 300 ]]; then
  echo "[error] failed to enqueue training task (http=$START_HTTP_CODE)" >&2
  cat "$START_RESPONSE_FILE" >&2
  exit 1
fi

TASK_ID=$(python3 - "$START_RESPONSE_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    payload = json.load(f)
print(payload.get('task_id', ''))
PY
)

if [[ -z "$TASK_ID" ]]; then
  echo "[error] task_id not found in enqueue response" >&2
  cat "$START_RESPONSE_FILE" >&2
  exit 1
fi

echo "[train] task_id=$TASK_ID"

declare FINAL_STATUS=""
declare FINAL_SUCCESSFUL=""
declare FINAL_DETAIL=""
declare FINAL_ERROR=""
declare RESULT_CONFIRMED_FROM_LOG="false"

declare START_EPOCH
START_EPOCH=$(date +%s)

while true; do
  STATUS_HTTP_CODE=$(curl -sS -o "$STATUS_RESPONSE_FILE" -w "%{http_code}" \
    "$BASE_URL/api/v1/ml/training/price-predictor/tasks/$TASK_ID")

  if [[ "$STATUS_HTTP_CODE" -lt 200 || "$STATUS_HTTP_CODE" -ge 300 ]]; then
    echo "[error] failed to query task status (http=$STATUS_HTTP_CODE)" >&2
    cat "$STATUS_RESPONSE_FILE" >&2
    exit 1
  fi

  IFS=$'\t' read -r CURRENT_STATUS CURRENT_READY CURRENT_SUCCESSFUL CURRENT_DETAIL CURRENT_ERROR <<EOF
$(python3 - "$STATUS_RESPONSE_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    payload = json.load(f)
status = str(payload.get('status', ''))
ready = 'true' if bool(payload.get('ready')) else 'false'
successful = 'true' if bool(payload.get('successful')) else 'false'
detail = str(payload.get('detail', '')).replace('\n', ' ').replace('\t', ' ').strip()
error = str(payload.get('error') or '').replace('\n', ' ').replace('\t', ' ').strip()
print("\t".join([status, ready, successful, detail, error]))
PY
)
EOF

  NOW_EPOCH=$(date +%s)
  ELAPSED=$((NOW_EPOCH - START_EPOCH))
  echo "[train] status=$CURRENT_STATUS ready=$CURRENT_READY successful=$CURRENT_SUCCESSFUL elapsed=${ELAPSED}s detail=\"$CURRENT_DETAIL\""

  if [[ "$CURRENT_READY" == "true" ]]; then
    FINAL_STATUS="$CURRENT_STATUS"
    FINAL_SUCCESSFUL="$CURRENT_SUCCESSFUL"
    FINAL_DETAIL="$CURRENT_DETAIL"
    FINAL_ERROR="$CURRENT_ERROR"
    break
  fi

  if (( ELAPSED >= POLL_TIMEOUT_SECONDS )); then
    WORKER_LOG_SNAPSHOT="$(docker compose logs --tail 600 training-worker || true)"
    if grep -Fq "Task ml.train_price_predictor[$TASK_ID] succeeded" <<<"$WORKER_LOG_SNAPSHOT"; then
      echo "[warn] status endpoint did not publish completion in time; confirmed task success from training-worker logs." >&2
      FINAL_STATUS="completed"
      FINAL_SUCCESSFUL="true"
      FINAL_DETAIL="confirmed via training-worker logs"
      RESULT_CONFIRMED_FROM_LOG="true"
      break
    fi
    if grep -Fq "Task ml.train_price_predictor[$TASK_ID] failed" <<<"$WORKER_LOG_SNAPSHOT"; then
      echo "[error] training task failed (detected from training-worker logs)" >&2
      echo "$WORKER_LOG_SNAPSHOT" >&2
      exit 1
    fi

    echo "[error] timeout waiting for training completion (${POLL_TIMEOUT_SECONDS}s)" >&2
    echo "[hint] check worker logs: docker compose logs --tail 200 training-worker" >&2
    exit 1
  fi

  sleep "$POLL_INTERVAL_SECONDS"
done

if [[ "$FINAL_SUCCESSFUL" != "true" ]]; then
  echo "[error] training task failed status=$FINAL_STATUS detail=\"$FINAL_DETAIL\" error=\"$FINAL_ERROR\"" >&2
  echo "[logs] recent training-worker logs:" >&2
  docker compose logs --tail 120 training-worker >&2 || true
  exit 1
fi

# Validate result payload and required artifact paths.
if [[ "$RESULT_CONFIRMED_FROM_LOG" == "true" ]]; then
  python3 - "$RELEASE_TAG" "$CREATE_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

release_tag = str(sys.argv[1]).strip()
create_manifest = str(sys.argv[2]).lower() == 'true'
repo_root = Path.cwd()

artifact_paths = [
  ("dataset_path", repo_root / "models" / "training-runs" / release_tag / "dataset.json"),
  ("summary_path", repo_root / "models" / "training-runs" / release_tag / "training-summary.json"),
  ("dataset_quality_path", repo_root / "models" / "training-runs" / release_tag / "dataset-quality.json"),
  ("comparison_report_path", repo_root / "models" / "training-runs" / release_tag / "artifact-comparison.json"),
  ("lstm_artifact_path", repo_root / "models" / "predictors" / "lstm" / f"{release_tag}.json"),
  ("ensemble_artifact_path", repo_root / "models" / "predictors" / "ensemble" / f"{release_tag}.json"),
]

if create_manifest:
  artifact_paths.append(("manifest_path", repo_root / "models" / "manifests" / f"{release_tag}.json"))

for key, path in artifact_paths:
  if not path.exists():
    print(f"[error] artifact missing: {key} -> {path}", file=sys.stderr)
    sys.exit(1)
  if path.stat().st_size <= 0:
    print(f"[error] artifact empty: {key} -> {path}", file=sys.stderr)
    sys.exit(1)
  with path.open(encoding="utf-8") as f:
    try:
      json.load(f)
    except Exception as exc:
      print(f"[error] artifact is not valid JSON: {key} -> {path} ({exc})", file=sys.stderr)
      sys.exit(1)

print('[verify] task completion confirmed from training-worker logs')
print(f'[verify] release_tag: {release_tag}')
print('[verify] artifacts:')
for key, path in artifact_paths:
  print(f'  - {key}: {path.relative_to(repo_root).as_posix()}')
PY
else
  python3 - "$STATUS_RESPONSE_FILE" "$CREATE_MANIFEST" <<'PY'
import json
import os
import sys
from pathlib import Path

status_file = Path(sys.argv[1])
create_manifest = str(sys.argv[2]).lower() == 'true'

with status_file.open(encoding='utf-8') as f:
    payload = json.load(f)

result = payload.get('result') or {}
if not isinstance(result, dict):
    print('[error] result payload is not an object', file=sys.stderr)
    sys.exit(1)

result_status = str(result.get('status') or '')
if result_status not in {'completed', 'skipped_insufficient_data'}:
    detail = str(result.get('detail') or payload.get('detail') or '').strip()
    print(f'[error] training result status is not completed: {result_status} ({detail})', file=sys.stderr)
    sys.exit(1)

repo_root = Path.cwd()

base_required_keys = [
    'dataset_path',
    'summary_path',
    'dataset_quality_path',
    'comparison_report_path',
]

required_keys = list(base_required_keys)
if result_status == 'completed':
  required_keys.extend([
    'lstm_artifact_path',
    'ensemble_artifact_path',
  ])

artifact_paths: list[tuple[str, Path]] = []
for key in required_keys:
    raw = result.get(key)
    if not raw:
        print(f'[error] missing result field: {key}', file=sys.stderr)
        sys.exit(1)
    p = Path(str(raw))
    artifact_paths.append((key, p if p.is_absolute() else (repo_root / p)))

manifest_path = None
if create_manifest and result_status == 'completed':
    manifest = result.get('manifest')
    if not isinstance(manifest, dict) or not manifest.get('manifest_path'):
        print('[error] create_manifest=true but manifest.manifest_path is missing', file=sys.stderr)
        sys.exit(1)
    p = Path(str(manifest['manifest_path']))
    manifest_path = p if p.is_absolute() else (repo_root / p)
    artifact_paths.append(('manifest_path', manifest_path))

for key, p in artifact_paths:
    if not p.exists():
        print(f'[error] artifact missing: {key} -> {p}', file=sys.stderr)
        sys.exit(1)
    if p.stat().st_size <= 0:
        print(f'[error] artifact empty: {key} -> {p}', file=sys.stderr)
        sys.exit(1)
    try:
        with p.open(encoding='utf-8') as f:
            json.load(f)
    except Exception as exc:
        print(f'[error] artifact is not valid JSON: {key} -> {p} ({exc})', file=sys.stderr)
        sys.exit(1)

release_tag = str(result.get('release_tag') or '')
print(f'[verify] training result status: {result_status}')
print(f'[verify] release_tag: {release_tag}')
if result_status == 'skipped_insufficient_data':
  detail = str(result.get('detail') or '').strip()
  print(f'[verify] note: {detail or "No usable historical bid-rate samples were available."}')
  print('[verify] predictor artifacts/manifest are intentionally skipped in this mode.')
print('[verify] artifacts:')
for key, p in artifact_paths:
    rel = p.relative_to(repo_root) if str(p).startswith(str(repo_root)) else p
    print(f'  - {key}: {rel}')
PY
fi

echo "[logs] recent training-worker logs"
docker compose logs --tail 80 training-worker || true

echo "[done] training and artifact verification succeeded"
