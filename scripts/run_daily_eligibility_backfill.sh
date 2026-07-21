#!/usr/bin/env bash
# 매일 KONEPS 하한율/자격(eligibility_raw) backfill + rule 라벨 갱신 + 리포트.
#
# 설계:
# - backfill 스크립트가 자체 쿼터 가드(429 abort, 연속 오류 중단, NULL 필터 멱등 재개)를
#   가지므로 매일 --limit 로 안전하게 청크 소진한다(운영자 후보 tier1 우선 정렬 내장).
# - dry-run 으로 잔여 0 이면 backfill 을 건너뛴다(쿼터 낭비 방지).
# - eligibility_saved=0 이면 원천 회귀 신호이므로 로그에 WARN 을 남긴다(수집 경로 점검 트리거).
# - 라벨 생성은 멱등 upsert(같은 project_id+source 갱신), 읽기 리포트는 부작용 없음.
# - 모든 시각은 KST. 외부 송신(Telegram) 없음. DB 쓰기는 승인된 backfill·라벨 범위만.
#
# crontab 예시(호스트 로컬 TZ=KST 해석):
#   17 7 * * * /usr/bin/flock -n /tmp/bid-vector-eligibility-backfill.lock \
#     /bin/bash /home/deploy/project/bid-vector/scripts/run_daily_eligibility_backfill.sh \
#     >> /home/deploy/project/bid-vector/logs/daily_eligibility_backfill.log 2>&1
set -euo pipefail

PROJECT_DIR=${PROJECT_DIR:-/home/deploy/project/bid-vector}
COMPOSE=${COMPOSE:-/usr/bin/docker compose}
LIMIT=${LIMIT:-700}
LOG_DIR="${PROJECT_DIR}/logs"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

stamp() { TZ=Asia/Seoul date --iso-8601=seconds; }
log() { echo "[eligibility-backfill] $*"; }

log "started_at=$(stamp) limit=${LIMIT}"

# 1) 잔여 확인 — 0 이면 backfill 스킵.
REMAINING=$(${COMPOSE} exec -T api python scripts/backfill_award_floor_rate.py --dry-run 2>/dev/null \
  | grep -oE 'target=[0-9]+' | head -1 | cut -d= -f2 || echo "unknown")
log "remaining_targets=${REMAINING}"

if [[ "${REMAINING}" == "0" ]]; then
  log "backfill 잔여 0 — 스킵"
else
  # 2) backfill 청크(자체 쿼터/오류 가드 내장).
  BACKFILL_OUT=$(${COMPOSE} exec -T api python scripts/backfill_award_floor_rate.py --limit "${LIMIT}" 2>&1 \
    | grep -E '^\[floor-backfill\] (target=|.*tier1_selected)' || true)
  echo "${BACKFILL_OUT}"
  # eligibility_saved=0 이면 원천 회귀 신호 — WARN(재실행하지 않음, 다음 사이클 재시도).
  if echo "${BACKFILL_OUT}" | grep -q 'eligibility_saved=0'; then
    log "WARN eligibility_saved=0 — 자격 수집 원천 회귀 의심(수집 경로 점검 필요)"
  fi
fi

# 3) rule 라벨 멱등 갱신(새로 적재된 공고 라벨링).
log "라벨 갱신"
${COMPOSE} exec -T api python scripts/generate_eligibility_labels.py 2>&1 \
  | grep -E '^\[generate-eligibility-labels\]' || true

# 4) 읽기 전용 리포트(증적 로그).
log "식별 리포트"
${COMPOSE} exec -T api python scripts/report_eligibility_precision.py 2>&1 \
  | grep -E '^\[eligibility-precision\]' || true

log "completed_at=$(stamp)"
