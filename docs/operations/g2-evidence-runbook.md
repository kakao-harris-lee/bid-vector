# G-2 Evidence Runbook

이 runbook은 G-2 exit 판단을 위한 운영 절차와 증적 체크리스트다. 목표는 새 SaaS 전환이 아니라, 현재 검증 환경에서 3개 이상 가상 사업자가 독립 `operator_id`/회사 프로필/전략/알림 증적을 유지하는지 N일 동안 반복 확인하는 것이다.

기본값은 항상 read-only 또는 dry-run이다. DB write, 실제 KONEPS 호출, 실제 Telegram 송신, 전략 적용은 운영 승인과 실행 창이 잡힌 뒤에만 수행한다.

## 현재 구현 상태

`50c9336` 기준으로 runbook 실행에 필요한 기반은 `main`에 반영되어 있다.

- `/api/v1/analytics/g2-evidence`: operator별 G-2 증적 ledger와 `blocking_gaps` 확인
- `/api/v1/operator/notification-channels`: operator별 masked notification route metadata 확인
- `/api/v1/synthetic/experiments/sample-gaps/candidates`: sample-gap 기반 실행 계획 확인
- `scripts/collect_g2_evidence.py`: operator 3개 이상에 대한 read-only HTTP evidence 파일을 `reports/g2-evidence/` 아래에 저장
- `jobs.collect_g2_evidence` / `COLLECT_G2_EVIDENCE_*`: 매일 22:00 KST에 operator별 G-2 ledger 요약을 하나의 `collect_g2_evidence` analytics event로 snapshot. 기본 OFF이며 operator data write, monitor 실행, 외부 호출, Telegram 송신 없음
- `scripts/run_g2_synthetic_evidence.py`: 기본 dry-run, 승인 후 `--write`로 synthetic evidence run enqueue
- `/admin/operations`: 관리자 surface에서 G-2 evidence summary 확인
- `/dashboard`: 사용자 surface에서 token owner 기준 투찰 판단에 집중
- synthetic experiment 결과는 `operator_id`가 붙어야 G-2 ledger에 operator-scoped evidence로 집계됨. slug-only 결과는 `mixed_scope`로 분류

운영 전 TODO:

1. 운영 DB에 notification channel migration 적용 여부 확인
2. 검증 대상 operator 3개 이상 선정
3. operator별 profile/strategy/channel 상태 저장
4. 일일 evidence 저장 경로는 `reports/g2-evidence/`로 생성
5. `COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED`를 켤지, 파일 수집만 할지 운영 창에서 결정
6. 실제 Telegram/app 송신 여부는 `dry_run_only` 상태로 먼저 검증

## 1. 운영 원칙

- `BASE_URL`, `TOKEN`, `EVIDENCE_DIR`, `DAY`를 매일 명시한다. 예: `DAY=2026-06-19`, `EVIDENCE_DIR=reports/g2-evidence/$DAY`.
- privileged operator 토큰으로만 cross-operator `operator_id` 조회/실행을 한다. 토큰이 없거나 권한이 없으면 canonical `operator`로 fallback될 수 있으므로 G-2 evidence로 쓰지 않는다.
- 모든 operator별 응답에서 `operator_id`, `current_operator_id`, `current_operator_username`이 의도한 대상과 일치해야 한다.
- synthetic/non-canonical operator evidence와 canonical G-0 smoke evidence를 같은 성공 근거로 합치지 않는다. `operator_scope`, `source_run_type`, `source_run_id`, `current_operator_id`가 있으면 함께 저장한다.
- 실제 외부 송신과 DB write를 기본 절차로 두지 않는다. 아래에서 "승인 후 실행"으로 표시된 단계만 운영자가 의식적으로 실행한다.

## 2. 3개 이상 가상 사업자 준비 조건

G-2 준비 완료 전제는 "존재"가 아니라 "반복 운영 가능한 독립 사업자"다. 최소 3개 operator가 아래 조건을 모두 만족해야 한다.

| 조건 | 확인 방법 | G-2 ready 기준 |
|---|---|---|
| 독립 계정 | `GET /api/v1/operator/accounts` 또는 `GET /api/v1/synthetic/operators` | 서로 다른 `operator_id`/`user_id`, `username`이 `synthetic-*`, `is_active=true` |
| 회사 프로필 | `GET /api/v1/operator/profile?operator_id=<OP_ID>` | `profile_configured=true`, 회사명/업종/면허/지역/매출 또는 수행능력 값 존재 |
| 감시 전략 | `GET /api/v1/operator/strategy?operator_id=<OP_ID>` | `strategy_configured=true`, 카테고리/지역/예산/임계값 중 사업자별 차이가 보임 |
| 알림 대상 | operator account sheet 또는 admin 화면의 notification mapping | Telegram/app target이 operator별로 분리되어 있거나, synthetic operator는 dry-run/skip 정책이 명시됨 |
| 증적 저장 위치 | `reports/g2-evidence/<day>/<run_id>/operator-<operator_id>/` 또는 수동 run 디렉터리 | profile, strategy, candidate preview, monitor, experiment, notification evidence 파일이 하루 단위로 저장됨 |

초기 카탈로그 확인:

```bash
python scripts/seed_synthetic_operators.py --dry-run
```

승인 후 DB에 시드:

```bash
python scripts/seed_synthetic_operators.py --out "$EVIDENCE_DIR/synthetic-operators.json"
```

이미 시드된 operator 목록 확인:

```bash
python scripts/seed_synthetic_operators.py --list > "$EVIDENCE_DIR/synthetic-operators-current.json"
curl "$BASE_URL/api/v1/operator/accounts" \
  -H "Authorization: Bearer $TOKEN" \
  > "$EVIDENCE_DIR/operator-accounts.json"
```

## 3. Operator별 프로필/전략/알림 대상 확인

매일 실행 전 대상 operator 목록을 고정한다.

```bash
export BASE_URL="http://localhost:3000"
export TOKEN="<PRIVILEGED_OPERATOR_TOKEN>"
export DAY="$(date -u +%F)"
export EVIDENCE_DIR="reports/g2-evidence/$DAY"
mkdir -p "$EVIDENCE_DIR"
```

read-only 파일 증적 수집은 아래 스크립트를 우선 사용한다. 이 스크립트는 GET 요청만 수행하고 DB, KONEPS, Telegram에는 쓰지 않는다.

```bash
python scripts/collect_g2_evidence.py \
  --base-url "$BASE_URL" \
  --token "$TOKEN" \
  --operator-id "<operator_id_1>" \
  --operator-id "<operator_id_2>" \
  --operator-id "<operator_id_3>" \
  --evidence-dir "$EVIDENCE_DIR" \
  --days 30
```

대상 목록을 파일로 관리할 때는 JSON 배열, `{"operator_ids": [...]}`, 또는 `{"operators": [{"operator_id": ...}]}` 형태를 사용한다.

```bash
python scripts/collect_g2_evidence.py \
  --base-url "$BASE_URL" \
  --token "$TOKEN" \
  --operators-file "$EVIDENCE_DIR/operator-ids.json" \
  --evidence-dir "$EVIDENCE_DIR" \
  --days 30
```

엔드포인트별 문제를 좁혀야 할 때는 아래 수동 수집을 반복한다. 이 경우에도 manifest에는 실제 생성된 경로를 그대로 기록한다.

```bash
export OP_ID="<operator_id>"
export RUN_ID="manual-$(date -u +%Y%m%dT%H%M%SZ)"
export RUN_DIR="$EVIDENCE_DIR/$RUN_ID/operator-$OP_ID"
mkdir -p "$RUN_DIR"

curl "$BASE_URL/api/v1/operator/profile?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$RUN_DIR/profile.json"

curl "$BASE_URL/api/v1/operator/strategy?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$RUN_DIR/strategy.json"

curl "$BASE_URL/api/v1/operator/dashboard?days=30&limit=5&operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$RUN_DIR/operator-dashboard.json"

curl "$BASE_URL/api/v1/analytics/operations-dashboard?days=30&recent_limit=5&operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$RUN_DIR/operations-dashboard.json"

curl "$BASE_URL/api/v1/operator/notification-channels?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$RUN_DIR/notification-channels.json"

curl "$BASE_URL/api/v1/analytics/g2-evidence?days=30&recent_limit=5&operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$RUN_DIR/g2-evidence.json"
```

확인 포인트:

- `profile.json`: `operator_id == OP_ID`, `profile_configured=true`.
- `strategy.json`: `operator_id == OP_ID`, `strategy_configured=true`, `review_threshold <= bid_now_threshold`.
- `operator-dashboard.json`: `current_operator_id == OP_ID`, 최근 `recent_monitor_runs[]`가 해당 사업자 실행만 포함.
- `operations-dashboard.json`: `operator_id/current_operator_id == OP_ID`, `strategy`, `notifications`, `smoke_test` evidence가 canonical-only인지 operator-scoped인지 구분 가능.
- `notification-channels.json`: `current_operator_id == OP_ID`, target은 masked label만 저장, `is_active`/`dry_run_only`/skip 정책이 operator별로 명확.
- `g2-evidence.json`: `operator_id/current_operator_id == OP_ID`, `evidence_status`와 `blocking_gaps[]`를 그대로 manifest에 반영.
- 알림 대상: Telegram chat id, app device token, channel id 같은 민감값은 원문 저장하지 않는다. 증적에는 masked id, channel status, `dry_run_only` 또는 `active` 여부만 남긴다.

## 4. 1일 실행 순서

### 4.0 Read-only G-2 evidence 수집과 daily snapshot

매일 먼저 `scripts/collect_g2_evidence.py`로 operator별 profile/strategy/notification channel/G-2 ledger 응답을 `reports/g2-evidence/$DAY/<run_id>/`에 저장한다. 이 파일 수집기는 exit review manifest의 기본 입력이다.

운영에서 `counted_days`를 자동으로 누적하려면 Celery beat에 아래 설정을 opt-in한다.

```dotenv
COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED=true
COLLECT_G2_EVIDENCE_HOUR_KST=22
COLLECT_G2_EVIDENCE_MINUTE=0
COLLECT_G2_EVIDENCE_WINDOW_DAYS=30
COLLECT_G2_EVIDENCE_RECENT_LIMIT=5
```

`jobs.collect_g2_evidence`는 canonical operator와 active `synthetic-*` operator를 훑고, compact summary를 **한 개의** `collect_g2_evidence` analytics event로 저장한다. 이 task는 strategy monitor를 실행하지 않고, operator data를 쓰지 않으며, 외부 KONEPS/Telegram 호출도 하지 않는다. 다만 analytics event를 DB에 남기므로 운영 DB에서 수동 실행하거나 schedule을 켤 때는 실행 창과 목적을 남긴다.

exit review에 scheduled snapshot을 근거로 쓰려면 해당 analytics event payload를 `reports/g2-evidence/$DAY/collect-g2-evidence.json` 같은 파일로 export해 manifest에 연결한다.

### 4.1 Scheduled smoke 확인

Scheduled smoke는 G-0 canonical 운영 안정성의 선행 신호다. G-2 per-operator ready를 대신하지 않는다.

```bash
python scripts/production_smoke_test.py \
  --base-url "$BASE_URL" \
  --bearer-token "$TOKEN" \
  --evidence-out "$EVIDENCE_DIR/smoke-read.json"
```

확인:

- `smoke-read.json`이 `status=passed`.
- `GET /api/v1/analytics/operations-dashboard`의 `smoke_test.current_streak`, 최신 phase `failure_category`, `retry_method`.
- scheduled smoke phase evidence의 `operator_scope`가 `canonical_only`이면 G-2 operator별 evidence로 계산하지 않는다.

승인 후 실제 KONEPS/monitor write smoke:

```bash
python scripts/production_smoke_test.py \
  --base-url "$BASE_URL" \
  --bearer-token "$TOKEN" \
  --write \
  --max-items 3 \
  --monitor-limit 3 \
  --evidence-out "$EVIDENCE_DIR/smoke-write.json"
```

`--write`는 KONEPS crawl과 strategy monitor를 실행한다. Telegram 송신 가능성이 있으므로 승인 없는 일일 기본 명령에 넣지 않는다.

### 4.2 Strategy monitor

먼저 read-only 후보 미리보기로 후보 없음과 전략 과필터링을 분리한다.

```bash
curl "$BASE_URL/api/v1/operator/strategy/candidates?operator_id=$OP_ID&limit=20&high_priority_only=true" \
  -H "Authorization: Bearer $TOKEN" \
  > "$EVIDENCE_DIR/$OP_ID/strategy-candidates.json"
```

승인 후 DB write/앱 알림 생성이 필요한 날에만 monitor를 실행한다.

```bash
curl -X POST "$BASE_URL/api/v1/operator/strategy/monitor?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "limit": 20,
    "high_priority_only": false,
    "max_active_bids": 3,
    "same_category_only": true,
    "similar_limit": 3,
    "min_similarity": 0.15
  }' \
  > "$EVIDENCE_DIR/$OP_ID/strategy-monitor.json"
```

실행 후 상세 저장:

```bash
export MONITOR_RUN_ID="<monitor_run_id>"
curl "$BASE_URL/api/v1/operator/strategy/monitor/runs/$MONITOR_RUN_ID?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$EVIDENCE_DIR/$OP_ID/strategy-monitor-$MONITOR_RUN_ID.json"
```

G-2 evidence로 인정하려면 `operator_id/current_operator_id == OP_ID`, `status=completed`, `evaluated_project_count`가 기록되어야 한다. `selected_candidate_count=0`은 실패가 아니라 "후보 없음"으로 분류하되, `strategy-candidates.json`과 strategy 필터를 함께 남긴다.

### 4.3 Decision experiment

권고 조회는 read-only다.

```bash
curl "$BASE_URL/api/v1/analytics/decision-recommendations?operator_id=$OP_ID&days=30&recommendation_limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  > "$EVIDENCE_DIR/$OP_ID/decision-recommendations.json"
```

새 experiment 등록은 DB write이므로 승인 후 실행한다. 보통 `decision-recommendations.json`의 `recommended_next_experiment`를 그대로 사용한다.

```bash
curl -X POST "$BASE_URL/api/v1/analytics/decision-experiments?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @decision-experiment-payload.json \
  > "$EVIDENCE_DIR/$OP_ID/decision-experiment-create.json"
```

매일 상태 조회:

```bash
curl "$BASE_URL/api/v1/analytics/decision-experiments?operator_id=$OP_ID&limit=20&sort=needs_attention" \
  -H "Authorization: Bearer $TOKEN" \
  > "$EVIDENCE_DIR/$OP_ID/decision-experiments.json"
```

재평가는 task/broker를 쓰므로 승인 후 실행하고 task status까지 저장한다.

```bash
curl -X POST "$BASE_URL/api/v1/analytics/decision-experiments/$EXPERIMENT_RUN_ID/evaluate?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  > "$EVIDENCE_DIR/$OP_ID/decision-experiment-evaluate.json"
```

전략 반영은 기본 dry-run만 수행한다.

```bash
curl -X POST "$BASE_URL/api/v1/analytics/decision-experiments/$EXPERIMENT_RUN_ID/apply-strategy?operator_id=$OP_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}' \
  > "$EVIDENCE_DIR/$OP_ID/decision-experiment-apply-strategy-dry-run.json"
```

`dry_run=false`는 G-2 exit review에서 성공 outcome, rollback 조건, operator owner 확인을 마친 뒤 별도 승인으로만 실행한다.

### 4.4 Synthetic experiment와 sample-gap

sample-gap 계획과 후보 생성은 read-only다.

```bash
curl "$BASE_URL/api/v1/synthetic/experiments/sample-gaps?max_runs=20" \
  > "$EVIDENCE_DIR/synthetic-sample-gaps.json"
```

gap이 있으면 후보 payload를 생성한다.

```bash
curl -X POST "$BASE_URL/api/v1/synthetic/experiments/sample-gaps/candidates" \
  -H "Content-Type: application/json" \
  -d '{
    "dimension": "category",
    "key": "software",
    "max_runs": 20,
    "action_code": "rerun_related_preset"
  }' \
  > "$EVIDENCE_DIR/synthetic-sample-gap-candidate.json"
```

확인:

- `run_allowed=true`여야 실행 가능.
- `blocked_by_warnings`에 `canonical_synthetic_mixed`가 있으면 중단하고 mixed data로 분류한다.
- `operator_slugs`가 3개 이상이고 canonical operator가 포함되지 않아야 한다.
- synthetic experiment 결과가 G-2 ledger에 operator별로 집계되려면 결과 metrics에 `operator_id`가 있어야 한다. 새 실행 결과는 upstream `user_id`를 `operator_id`로 mirror하지만, 과거 slug-only 결과는 `mixed_scope`로 분류한다.

`next_step`별 write 작업은 모두 승인 후 실행한다.

| `next_step` | 승인 후 조치 |
|---|---|
| `run_existing_experiment` | 응답의 `experiment_id`를 `SYNTHETIC_EXPERIMENT_ID`로 잡고 run 생성 |
| `save_preset` | `POST /api/v1/synthetic/experiments/presets/<preset_name>`으로 saved experiment 생성/갱신 후 run 생성 |
| `create_experiment` | 응답의 `experiment_payload`를 `POST /api/v1/synthetic/experiments`에 전달한 뒤 새 `experiment_id`로 run 생성 |
| `resolve_mixed_data` | 실행하지 않고 mixed data로 분류 |

승인 후 기존 experiment를 실행:

```bash
curl -X POST "$BASE_URL/api/v1/synthetic/experiments/$SYNTHETIC_EXPERIMENT_ID/runs" \
  > "$EVIDENCE_DIR/synthetic-experiment-run.json"
```

상태 폴링:

```bash
curl "$BASE_URL/api/v1/synthetic/experiments/$SYNTHETIC_EXPERIMENT_ID/runs/$SYNTHETIC_RUN_ID" \
  > "$EVIDENCE_DIR/synthetic-experiment-run-$SYNTHETIC_RUN_ID.json"
```

CLI backtest는 기본적으로 결과 파일만 쓰고 paper bid rows는 저장하지 않는다. DB에 paper-bid run을 남겨야 할 때만 `--persist`를 사용한다.

```bash
python scripts/backtest_synthetic_operators.py \
  --start-date 2025-01-01 \
  --end-date 2025-12-31 \
  --limit 200 \
  --operators sw-small-seoul,sw-mid-metro,sw-large-national \
  --out-dir "$EVIDENCE_DIR/synthetic-backtest"
```

## 5. Telegram/app 알림 dry-run과 실제 송신 구분

| 경로 | Dry-run/read-only 증적 | 실제 송신/write 증적 | G-2 판정 |
|---|---|---|---|
| Strategy candidates | `/strategy/candidates` 응답. DB write와 알림 없음 | 없음 | 후보 탐색만 확인 |
| Strategy monitor | 실행 전에는 없음 | `notification_count`, `notification_id`, app notification row 생성 | operator별 app 알림 증적 |
| Telegram canonical | `production_smoke_test.py` without `--write`는 상태만 확인 | `--write` 또는 Telegram route가 `sent`/message id 기록 | G-0 또는 canonical 증적 |
| Telegram synthetic/non-canonical | `skipped_synthetic_operator` 또는 `skipped_non_canonical_operator`, "recorded only" detail | 현재 기본 정책상 실제 송신하지 않음 | dry-run/skip evidence로 인정, 실제 혼합 송신 금지 |
| Decision apply | `dry_run=true`, `applied=false` | `dry_run=false`, `applied=true` | dry-run은 검토 증적, 실제 적용은 별도 승인 필요 |
| Synthetic sample-gap | `/sample-gaps`, `/candidates`는 read-only | experiment run 생성/큐잉 | sample-gap 연결 증적 |

실제 Telegram/app 송신을 G-2 evidence로 인정하려면 다음을 같이 남긴다.

- target `operator_id`와 notification owner가 일치.
- Telegram chat/channel/app device target이 operator별로 분리되어 있거나, synthetic operator는 dry-run/skip으로 기록.
- 민감값은 masked id만 저장.
- canonical Telegram chat으로 synthetic/non-canonical operator 알림이 전송되지 않음.

## 6. 1일 단위 evidence checklist

하루가 끝날 때 operator별 체크리스트를 채운다.

| 항목 | 파일/필드 | 통과 기준 | 실패 분류 |
|---|---|---|---|
| Operator roster | `operator-accounts.json` | G-2 대상 3개 이상, `is_synthetic=true`, `is_active=true` | mixed data |
| Profile | `operator-<OP_ID>/profile.json` | `operator_id == OP_ID`, `profile_configured=true` | mixed data |
| Strategy | `operator-<OP_ID>/strategy.json` | `strategy_configured=true`, 임계값 유효 | 후보 없음 |
| Notification target | `operator-<OP_ID>/notification-channels.json` | `is_active` 또는 dry-run/skip 정책이 operator별로 명확 | Telegram/app notification |
| Read-only evidence collection | `g2-evidence-summary.json`, `run-metadata.json` | 3개 이상 operator, `write_performed=false`, endpoint별 raw file 존재 | credential, mixed data, missing evidence |
| G-2 evidence ledger | `operator-<OP_ID>/g2-evidence.json` | `evidence_status`, 영역별 status, `blocking_gaps[]` 저장 | mixed data, missing evidence |
| Daily evidence snapshot | `collect_g2_evidence` analytics event 또는 export JSON | `operator_count >= 3`, `error_count=0`, per-operator status 저장 | task/broker, missing evidence |
| Scheduled smoke | `smoke-read.json`, dashboard `smoke_test` | read-only smoke pass, scheduled failure 없음 또는 원인 분류됨 | credential, KONEPS 응답, task/broker |
| Candidate preview | `operator-<OP_ID>/strategy-candidates.json` | API 성공, `current_operator_id == OP_ID` | 후보 없음 |
| Strategy monitor | `operator-<OP_ID>/strategy-monitor*.json` | 승인 실행 시 completed, run id 저장 | 후보 없음, Telegram/app notification, task/broker |
| Decision experiment | `operator-<OP_ID>/decision-experiments.json` | planned/running/completed 상태와 sample count 확인 | 후보 없음, task/broker |
| Decision apply dry-run | `decision-experiment-apply-*-dry-run.json` | `dry_run=true`, `applied=false`, owner 일치 | mixed data |
| Synthetic sample-gap | `synthetic-sample-gaps.json` | warnings 검토, gap action 결정 | mixed data |
| Synthetic run | `synthetic-experiment-run*.json` 또는 backtest dir | synthetic-only, 3개 이상 operator slug, `operator_id`-scoped result, sample report 존재 | mixed data, task/broker |
| 알림 혼합 방지 | operations dashboard notifications | synthetic/non-canonical Telegram은 skipped/dry-run, app notification owner 일치 | Telegram/app notification, mixed data |

일일 결과 요약 템플릿:

```markdown
## G-2 Daily Evidence: YYYY-MM-DD

- 대상 operator_id: <id1>, <id2>, <id3>
- scheduled smoke: pass/fail, current_streak=<n>, source_scope=<canonical_only/operator>
- strategy monitor: <completed>/<failed>/<skipped>, 주요 run_id
- decision experiment: planned/running/completed, sample 부족 여부
- synthetic experiment: run_id, synthetic_only=true/false, sample-gap status
- notification: app count, Telegram sent/skipped/dry-run, mixed routing 여부
- G-2 evidence ledger: ready/insufficient/missing/mixed_scope, blocking_gaps=<count>
- 실패 분류: credential / KONEPS 응답 / 후보 없음 / Telegram/app notification / task/broker / mixed data / none
- 재실행 계획: <command or dashboard action>
```

## 7. 실패 원인 분류와 조치

| 분류 | 의미 | 먼저 볼 증적 | 재실행/조치 |
|---|---|---|---|
| `credential` | API 토큰, KONEPS key, Telegram token/chat, DB secret 누락 또는 거부 | HTTP 401/403, smoke `failure_category`, server log | secret 수정 후 read-only smoke, 승인 시 write smoke 재실행 |
| `KONEPS 응답` | KONEPS timeout, 장애, 응답 schema 변화, 결과 0건 | crawl phase evidence, `koneps_collect`, crawl log | KONEPS 정상화 확인 후 `--write --max-items 3` 승인 실행 |
| `후보 없음` | 전략은 실행됐지만 대상 공고 또는 선택 후보가 없음 | candidate preview `returned_candidate_count`, monitor `selected_candidate_count`, `skip_reason` | 전략 필터/기간/카테고리 넓힌 뒤 preview부터 재확인 |
| `Telegram/app notification` | app notification owner 불일치, Telegram 설정 실패, synthetic 송신 skip 미기록 | `notification_count`, Telegram status counts, delivery detail | target mapping 수정. synthetic은 실제 송신 대신 skipped/dry-run evidence 확인 |
| `task/broker` | Celery broker/backend/worker, ML queue, async task timeout | operations dashboard `tasks`, poll URL status, worker log | worker/broker 복구 후 같은 task 또는 API 재실행 |
| `mixed data` | canonical/synthetic 또는 operator scope가 섞여 G-2 판단 불가 | `operator_id` mismatch, `canonical_synthetic_mixed`, `operator_scope=canonical_only`, slug-only synthetic result without `operator_id` | 해당 run을 G-2 evidence에서 제외하고 synthetic-only/operator-scoped로 재실행 |

분류가 애매하면 `mixed data`로 올려서 exit review에서 제외한다. G-2 ready는 모호한 성공보다 재현 가능한 실패 분류를 우선한다.

## 8. G-2 exit review template

상세 review 양식과 evidence manifest contract는 `docs/operations/g2-exit-review-template.md`를 사용한다. 이 runbook의 일일 산출물은 해당 template의 `manifest.json` 입력이다.

권장 review 산출물:

- `reports/g2-evidence/<review_id>/manifest.json`: operator별 profile/strategy/channel/evidence path, 날짜별 status, `blocking_gaps` 처리 상태, dry-run/승인 후 실행 항목을 구조화한다.
- `reports/g2-evidence/<review_id>/exit-review.md`: manifest를 근거로 G-2 exit gate별 pass/fail과 최종 `approve`/`hold`를 적는다.

현재 실제 N일 증적이 없으면 `approve` 또는 `hold`를 미리 선언하지 않는다. Review 준비 문서의 기본 상태는 `pending` 또는 `draft`다.

## 9. 완료 판정 기준

G-2 exit를 선언하려면 최소 N일 동안 아래를 모두 만족해야 하며, 세부 `approve`/`hold` 기준은 `docs/operations/g2-exit-review-template.md`를 따른다.

- 3개 이상 synthetic operator의 `operator_id`, profile, strategy, notification policy가 독립적으로 확인됨.
- operator별 strategy candidate/monitor/decision experiment evidence가 `current_operator_id` 기준으로 분리됨.
- synthetic experiment 또는 backtest evidence가 3개 이상 operator slug를 포함하고 `operator_id`-scoped 결과를 남기며, `synthetic_only=true` 또는 canonical mixed warning 없음.
- Telegram/app notification evidence가 실제 송신과 dry-run/skip을 구분하며, canonical chat으로 synthetic 알림이 섞이지 않음.
- 실패가 있더라도 위 6개 분류 중 하나로 분류되고, retry command 또는 보류 사유가 남음.
- `/api/v1/analytics/g2-evidence`의 `blocking_gaps`가 operator별로 resolved 또는 excluded 처리되어 있고, unresolved gap을 성공 근거로 사용하지 않음.
