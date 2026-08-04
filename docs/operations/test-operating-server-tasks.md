# Test and Operating Server Tasks

기준일: 2026-08-03

이 문서는 `docs/roadmap.md`의 남은 작업 중 테스트/운영 서버에서 진행해야 의미가 있는 항목을 분리한다. 기준은 실제 DB, KONEPS 응답, scheduler/worker, Telegram/app target, SMTP/provider, Supabase staging, 실제 사용자 또는 실제 사업자 데이터가 있어야 검증 가능한지 여부다.

## 진행 원칙

- 기본값은 read-only 또는 dry-run이다.
- DB write, 실제 KONEPS 호출, Telegram/app 실송신, 전략 적용, 메일 실발송은 운영 승인과 실행 창이 있을 때만 수행한다.
- `operator_id`, `current_operator_id`, `current_operator_username`, `operator_scope`, `source_run_type`, `source_run_id`를 증적에 남긴다.
- raw Telegram chat id, app device token, 사업자 민감정보, email 원문 수신자는 증적에 남기지 않는다.

## G-0/G-2 운영 증적

- G-0 scheduled smoke 7일 이상 관찰: 실제 KONEPS key, scheduler, worker, broker, Telegram/app 환경에서 핵심 phase green을 확인한다.
- G-2 N일 증적 축적: 3개 이상 synthetic operator의 profile, strategy, notification channel, candidate, decision, G-2 evidence를 `reports/g2-evidence/`에 매일 저장한다.
- `COLLECT_G2_EVIDENCE_*` snapshot 운영: 운영 DB에 analytics event를 남기므로 실행 창과 목적을 기록한다.
- G-2 blocking gap 해소: `/api/v1/analytics/g2-evidence`의 unresolved gap을 operator별로 `resolved` 또는 `excluded` 처리한다.
- G-2 exit review: 여러 일자의 manifest draft를 모아 `manifest.json`, `exit-review.md`, readiness result를 생성하고 human review로 approve/hold를 결정한다.

## 실제 실행/알림/수집

- sample-gap `--write` 실행: 운영 승인 후 synthetic evidence run을 enqueue하고 operator-scoped result가 남는지 확인한다.
- strategy monitor 실제 실행: 후보 생성, app notification row, monitor run, task 상태가 operator별로 분리되는지 확인한다.
- notification target 검증: 실제 Telegram/app target, `dry_run_only`, secret resolver, masking 정책을 operator별로 확정한다.
- KONEPS 공고/개찰 안정성: live response, timeout, schema 변화, 결과 0건, notice number canonicalization을 운영 수집 데이터로 판정한다.
- `TenderResult`/`Project` 정합: 운영 DB의 최신 공고/개찰/낙찰 결과 연결 상태를 확인한다.

## Phase 3 실증

- 엔지니어링협회 가입 회사 실증: 최소 1개 실제 회사가 협회 가입/기술부문/전문분야 조건이 명시된 공고 추천을 매일 검토한다.
- 사용자 피드백 축적: 적합/부적합/보류, 투찰/검토/보류 사유, 놓친 유효 공고, 검토 시간 감소를 측정한다.
- 사업자번호 진위/상태 확인: 국세청 API key, 실제 사업자번호, 외부 조회 정책으로 휴폐업/과세유형/진위 확인을 검증한다.
- 투찰 보고서 메일 실발송: SMTP/provider credential, 수신자 masking, delivery telemetry, bounce/failure 처리를 staging/운영에서 확인한다.

## 추천 품질/ML 검증

- 최신 낙찰 holdout: 실제 수집 DB에서 최신 N건, 업무구분별 wide holdout, 해양/엔지니어링 고정 20건을 재실행한다.
- 기관/수요처 group holdout: 기관별 패턴을 외우는지 확인하기 위해 group holdout 성능을 분리한다.
- 가격 레짐 calibration: `floor_bound`, `near_100`, `deep_discount`, `ambiguous`별 오차, selector hit, worst case를 실제 이력으로 평가한다.
- 데이터 품질 flag 검증: `low_actual_rate`, `amount_rate_mismatch`, denominator mismatch 표본을 clean 표본과 분리한다.
- 알림 품질 조정: 실제 클릭, 확인, 보류, 투찰 결정, 알림 피로도를 기반으로 threshold를 조정한다.

## ML-UX runtime 성능

- `docs/operations/ml-ux-performance-improvement-plan.md`의 측정 CLI로 HTTP p95/p99,
  ops/inference queue wait p95/p99, API/worker/inference-worker RSS 기준선을 같은 git
  SHA에서 수집한다.
- idle 측정과 승인된 synthetic operator preview 부하 측정을 분리하고, 운영 사용자나 실제
  알림 대상에는 부하 작업을 실행하지 않는다.
- GET similarity가 API에서 모델을 로드하지 않는지, preview 부하 중 ops 큐가 inference
  runtime만큼 지연되지 않는지를 개선 전후 동일 명령으로 검증한다.
- 서버 판정 전까지 로컬 수치를 운영 SLO 달성 근거로 사용하지 않는다.

## 수집 → 분석 → 적재 E2E handoff

이 검증은 실제 KONEPS 응답, PostgreSQL/pgvector, RabbitMQ, beat, 전용 worker와
학습 완료된 signed release가 함께 있는 테스트/운영 서버에서 이어간다. 개발 장비의 mock
수집이나 미학습 heuristic 결과는 E2E 통과 근거로 사용하지 않는다. 기준 코드는
`refactor/architecture-boundaries` 브랜치이며 실행 전에 서버의 git SHA를 증적에 남긴다.

### 통제 실행 설정

첫 실행은 schedule 중복을 피하기 위해 수집·monitor schedule을 끄고 수동 smoke 한 번으로
검증한다. 아래는 값 자체가 아니라 필요한 설정 상태이며 secret은 출력하거나 증적에 넣지
않는다.

```dotenv
CELERY_ALLOW_INLINE_ML_TASKS=false
INFERENCE_OUTBOX_SCHEDULE_ENABLED=true
STALE_TASK_RECONCILER_SCHEDULE_ENABLED=true
KONEPS_COLLECTION_SOURCE=koneps-openapi
KONEPS_COLLECTION_EXECUTION_MODE=auto
KONEPS_SCSBID_COLLECTION_SOURCE=scsbid-openapi
KONEPS_SCSBID_COLLECTION_EXECUTION_MODE=auto
KONEPS_COLLECTION_SCHEDULE_ENABLED=false
KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED=false
OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED=false
ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE=true
```

API, `worker`, `inference-worker`, `ml-worker`, `training-worker`, `beat`, RabbitMQ와 DB를
기동하고 다음 gate를 먼저 통과시킨다.

1. `python scripts/checked_alembic.py --expected-database <database> --check-only`로
   비밀값 없는 DB fingerprint와 현재 revision을 기록한 뒤, 같은 명령에서 `--check-only`를
   빼고 migration을 적용한다. 적용 후 repository head가 아니면 중단한다.
2. `python scripts/promote_ml_release.py preflight-rollout --manifest <release> --require-signature --production --expected-git-sha "$(git rev-parse HEAD)"`
   가 성공하고 manifest의 git SHA, artifact checksum, model path가 현재 배포와 일치한다.
3. ops/inference/backfill/training/reevaluation queue별 전용 worker가 응답하며
   `inference-worker`가 embedding/similarity task를 등록했다. 배포 시 beat는 중지한 채
   runtime을 먼저 재생성하고 `inspect registered`와 `inspect active_queues`가 통과한 뒤
   마지막에 기동한다(`scripts/sync-after-merge.sh`).
4. 승인된 격리 operator와 notification dry-run 또는 안전한 테스트 target을 사용한다.
5. 대상 DB를 백업하고 notice/project/decision/notification 기준 count를 먼저 기록한다.

### 한 번의 추적 가능한 검증

```bash
python scripts/production_smoke_test.py \
  --base-url https://<test-api-host> \
  --write \
  --max-items 3 \
  --monitor-limit 3 \
  --evidence-out reports/g2-evidence/pipeline-e2e-<git-sha>.json
```

같은 소량 notice 집합을 따라 아래 불변식을 확인한다.

1. **수집/정규화:** `CrawlJob`의 live 응답 건수, 정규화 성공 건수, 탈락 건수와 사유가
   합계로 맞고 `fallback_mock`/mock 결과가 canonical `Project` 후보에 들어오지 않는다.
2. **원자 적재:** 각 notice가 `Project`, `HistoricalData`/`TenderResult`와 필요한
   `semantic_input.changed` outbox row로 연결된다. 재실행해도 notice별 canonical row가
   중복 생성되지 않는다.
3. **추론 projection:** outbox가 terminal `processed`가 되고 현재 semantic fingerprint와
   embedding/model identity가 일치한 뒤에만 similarity edge/read model이 준비된다. failed,
   stale, missing projection이 하나라도 있으면 monitor를 실행하지 않는다.
4. **분석/결정:** 격리 operator의 monitor가 저장된 projection을 소비하고 candidate별 분석을
   한 번만 수행한다. `evaluated >= selected >= persisted`를 만족하고 모든 persisted decision을
   하나의 `monitor_run_id`로 추적할 수 있어야 한다.
5. **알림/완료:** decision, notification, delivery와 monitor run의 성공/실패 상태가 모순되지
   않는다. 중간 오류 뒤 일부 decision/notification만 commit된 경우 E2E 실패로 판정한다.
6. **성능:** 같은 SHA와 학습 release에서
   `scripts/measure_runtime_performance.py`로 HTTP p95/p99, queue wait p95/p99, API/worker RSS를
   수집한다. 절차와 container 이름은
   `docs/operations/ml-ux-performance-improvement-plan.md`를 따른다.

증적에는 git SHA, release tag/checksum, 격리 operator id, crawl/monitor run id, notice 수,
source→destination count, outbox 상태/지연, decision/notification count와 p95/p99/RSS만 남긴다.
token, DB URL, raw 사업자정보, raw 알림 target, 원문 KONEPS payload는 남기지 않는다.

### 중단 기준과 후속 구현 우선순위

다음 중 하나면 schedule을 켜지 말고 row와 log를 삭제하지 않은 채 증적을 보존한다:
`fallback_mock` 유입, 설명 없는 item drop, 동일 notice 중복, outbox failed/stale, projection 이전
분석, monitor partial commit, 학습 release 불일치, 실제 사용자 알림 발송. 운영 데이터에서
강제 cleanup이나 fault injection은 하지 않는다.

검증에서 재현되면 다음 순서로 별도 변경한다.

1. P0: mock/fallback origin을 canonical production 후보에서 격리하고 수집 provenance를 저장한다.
2. P1: item receipt/drop reason과 notice DB unique key를 추가하고 projection readiness barrier를
   monitor 앞에 둔다.
3. P1: decision에 `monitor_run_id`를 연결하고 decision/notification/run의 commit 또는 durable
   checkpoint 경계를 일관되게 만든다.
4. P1: 수집/분석 task retry, dead-letter/visibility와 stale reconciler 운영 정책을 확정한다.
5. P2: raw payload/Celery result 중복 및 JSON Text 이중 decode를 줄이고 heuristic 추천 계약을
   ML predictor와 명확히 분리한다.

### 다음 작업자용 짧은 프롬프트

```text
bid-vector의 refactor/architecture-boundaries 최신 커밋을 ML 학습 완료 테스트 서버에 배포해
docs/operations/test-operating-server-tasks.md의 "수집 → 분석 → 적재 E2E handoff"를 수행해줘.
signed release preflight와 read-only 점검 후 격리 operator로 max-items=3 수동 smoke만 실행하고,
수집 count/탈락 사유 → canonical rows/outbox → embedding·similarity readiness → monitor
decision/notification을 같은 notice/run으로 추적해. fallback_mock, silent drop, duplicate notice,
stale projection, partial commit이면 즉시 중단하고 증적을 보존해. 같은 SHA/release에서 HTTP
p95/p99, queue wait p95/p99, API·worker RSS도 측정한 뒤 원인·재현 절차·P0/P1 수정안을 보고해.
승인 없이 schedule 활성화, 실제 사용자 알림, 운영 데이터 삭제, push/merge는 하지 마.
```

## 원격 데이터/모델 전환

- Supabase staging migration rehearsal: dump/restore, row count, schema drift, FK 정합, holdout, smoke test를 staging DB에서 검증한다.
- 모델 아티팩트 Storage 이전: private bucket, checksum, release manifest, mirror export, restore 가능성을 확인한다.
- runtime 설정 분리: API/worker/inference-worker/training-worker의 DB 연결, pooler mode, active manifest URI, local cache dir를 환경별로 검증한다.
- 비용/호출량 모니터링: KONEPS/API 호출량, Telegram/app 알림량, ML inference 비용, 메일 발송량을 tenant/operator별로 관찰한다.

## SaaS/정산 검증

- tenant isolation/RBAC/audit log는 실제 또는 staging workload로 cross-tenant 접근 차단을 확인한다.
- bid lifecycle은 실제 사용자 결정, 외부 나라장터 제출 기록, 개찰 결과, 낙찰/유찰 결과와 연결해 검증한다.
- 수수료/정산은 낙찰 확정, 계약금액, 취소/무효, 세금계산서, 지급 상태, 분쟁 처리를 staging 업무 흐름으로 검증한다.
- 성공보수형 모델은 계약/법무/세무 검토 전까지 운영 실험 가설로만 관리한다.

## 완료 기준

- 증적 파일은 `reports/g2-evidence/` 또는 명시된 staging evidence path에 남는다.
- 성공 근거와 제외 근거가 manifest에 분리되어야 한다.
- 운영 서버에서만 판정 가능한 항목을 개발 노트북 테스트 성공으로 대체하지 않는다.
