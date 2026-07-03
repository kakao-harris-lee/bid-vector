#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR=${PROJECT_DIR:-/home/deploy/project/bid-vector}
VERIFY_DATE=${1:-}
if [[ "${VERIFY_DATE}" == "--date" ]]; then
  VERIFY_DATE=${2:-}
fi
if [[ -z "${VERIFY_DATE}" ]]; then
  VERIFY_DATE=$(TZ=Asia/Seoul date +%Y-%m-%d)
fi

COMPOSE=${COMPOSE:-/usr/bin/docker compose}
LOG_DIR="${PROJECT_DIR}/logs"
DATE_COMPACT=${VERIFY_DATE//-/}
RUN_ID="${DATE_COMPACT}T2210KST-fastlane"
EVIDENCE_ROOT="reports/g2-evidence/${VERIFY_DATE}-fastlane-scheduled"
RUN_DIR="${EVIDENCE_ROOT}/${RUN_ID}"
REVIEW_ID="g2-fastlane-${DATE_COMPACT}"
REVIEW_DIR="reports/g2-evidence/${REVIEW_ID}"
READINESS_PATH="${REVIEW_DIR}/readiness.json"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

echo "[g2-fastlane] started_at=$(TZ=Asia/Seoul date --iso-8601=seconds) verify_date=${VERIFY_DATE}"
echo "[g2-fastlane] run_id=${RUN_ID}"

${COMPOSE} ps api worker beat

${COMPOSE} exec -T api python scripts/run_g2_fastlane_evidence.py --skip-monitor

${COMPOSE} exec -T \
  -e RUN_ID="${RUN_ID}" \
  -e EVIDENCE_ROOT="${EVIDENCE_ROOT}" \
  api sh -c '
set -e
TOKEN=$(python - <<PY
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.core.single_user import ensure_operator_account

db = SessionLocal()
try:
    operator = ensure_operator_account(db)
    print(create_access_token({"sub": str(operator.id), "type": "access"}))
finally:
    db.close()
PY
)
python scripts/collect_g2_evidence.py \
  --base-url http://127.0.0.1:3000 \
  --token "$TOKEN" \
  --operator-id 19 \
  --operator-id 20 \
  --operator-id 25 \
  --evidence-dir "$EVIDENCE_ROOT" \
  --days 30 \
  --run-id "$RUN_ID" \
  --timeout-seconds 90 \
  --fail-on-blocking-gaps
'

${COMPOSE} exec -T api python scripts/build_g2_exit_review.py \
  --evidence-root "${RUN_DIR}" \
  --output-dir "${REVIEW_DIR}" \
  --review-id "${REVIEW_ID}" \
  --min-days 1 \
  --min-operators 3

${COMPOSE} exec -T api python scripts/check_g2_exit_readiness.py \
  --manifest "${REVIEW_DIR}/manifest.json" \
  --output "${READINESS_PATH}" \
  --min-days 1 \
  --min-operators 3

${COMPOSE} exec -T api python -m json.tool "${READINESS_PATH}"

echo "[g2-fastlane] completed_at=$(TZ=Asia/Seoul date --iso-8601=seconds) readiness=${READINESS_PATH}"
