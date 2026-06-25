# G-2 exit 병렬 작업 완료 기록

기준 커밋: `06eeee5`
계획 작성일: 2026-06-19
완료 커밋: `7fdc04c`
완료일: 2026-06-19
기준 로드맵: `docs/roadmap.md` Phase 2 / G-2

이 문서는 G-2 완료 조건을 채우기 위한 병렬 작업의 완료 기록입니다. 현재 기준의 다음 TODO와 exit 판단은 `docs/roadmap.md`와 `docs/operations/g2-evidence-runbook.md`를 우선합니다.

## 완료 요약

`7fdc04c` 기준으로 다음 작업이 `main`에 병합되었습니다.

| 에이전트 | 커밋 | 완료 결과 | 남은 TODO |
|---|---|---|---|
| A. G-2 증적 ledger/API | `b3ee613` + `a55a93c` | `/api/v1/analytics/g2-evidence` 추가, smoke/monitor/decision/synthetic/notification 증적 ledger 제공, smoke scope 리뷰 수정 | N일 operator별 evidence 저장, `blocking_gaps` 운영 관리 |
| B. 사업자별 알림 대상 매핑 | `219c70e` | `OperatorNotificationChannel` 모델/migration, masked `/operator/notification-channels`, dry-run route evidence | 운영 DB migration 적용, non-canonical 실제 송신 secret resolver/target 정책 확정 |
| C. sample-gap 기반 synthetic 증적 실행 | `f5f97ea` | sample-gap `execution_plan`, `scripts/run_g2_synthetic_evidence.py`, run summary source context | dry-run 검토 후 승인된 `--write` 실행으로 settled sample 축적 |
| D. 관리자/사용자 surface 분리 | `b9be656` | `/admin/operations` G-2 evidence summary, `/dashboard` token-owner 사용자 surface 제한 | 실제 운영 화면에서 operator별 evidence UX 확인 |
| E. G-2 운영 runbook | `c0d32d0` | `docs/operations/g2-evidence-runbook.md` 작성 | runbook대로 N일 evidence 수집 후 exit review 작성 |

통합 검증:

- backend 선택 테스트 201개 통과
- frontend 테스트 24개 통과
- frontend production build 통과
- `python -m py_compile` 통과
- schema drift/migration test 통과
- `git diff --check` 통과

## 현재 TODO

1. 운영/검증 DB에 `6f2a8c9d0e12_add_operator_notification_channels.py` migration 적용 절차를 확인한다.
2. 3개 이상 가상 사업자를 선정하고 profile, strategy, notification channel 상태를 일 단위로 저장한다.
3. `scripts/collect_g2_evidence.py` 또는 `/api/v1/analytics/g2-evidence?operator_id=<OP_ID>`로 `reports/g2-evidence/`에 일일 증적을 저장하고, 필요한 경우 `COLLECT_G2_EVIDENCE_*` snapshot으로 counted day를 축적한다. `blocking_gaps`는 operator별 TODO로 관리한다.
4. `scripts/run_g2_synthetic_evidence.py --dry-run` 결과를 검토한 뒤, 승인된 경우에만 `--write`로 operator_id-scoped synthetic evidence run을 enqueue한다.
5. non-canonical 실제 Telegram/app 송신 전까지는 `dry_run_only` 또는 skip evidence를 유지한다.
6. `docs/operations/g2-exit-review-template.md`의 manifest/checklist로 G-2 완료 여부를 판정한다.

## G-2 완료 조건

Exit gate G-2:

1. 3개 이상 가상 사업자가 독립 ID/사업자 정보/전략으로 운영됨
2. 각 사업자의 공고 추천과 알림이 서로 섞이지 않음
3. 관리자 화면에서 사업자별 백테스트, smoke, 통계, 수집 상태를 구분해 볼 수 있음
4. 사용자 화면은 관리 기능 없이 투찰 판단에 집중함

이번 병렬 작업의 완료 기준은 G-2 exit gate를 바로 선언하는 것이 아니라, 위 조건을 판단할 수 있는 API, 화면, 알림 라우팅, 실행 증적, 운영 절차를 갖추는 것이었습니다. 이 기준은 `7fdc04c`에서 충족되었습니다. 실제 G-2 exit는 N일 운영 증적을 수집한 뒤 별도 review로 판정합니다.

## 원본 작업 지시서

아래 내용은 `06eeee5`에서 병렬 작업을 시작할 때 사용한 원본 지시서입니다. 새 작업을 시작할 때는 그대로 재사용하지 말고 `docs/roadmap.md`의 최신 "다음 우선순위"와 `docs/operations/g2-evidence-runbook.md`의 TODO를 기준으로 새 계획을 작성합니다.

## 공통 진행 규칙

1. 모든 에이전트는 최신 `origin/main`에서 독립 worktree와 독립 브랜치를 만든다.
2. `main` worktree에서는 비-trivial 파일을 수정하지 않는다.
3. 서로의 worktree나 브랜치를 수정하지 않는다.
4. DB write, 실제 KONEPS 호출, 실제 Telegram 송신, 운영 배포, 원격 push/merge는 사용자 승인 없이 하지 않는다.
5. 마이그레이션이 필요한 작업은 Alembic migration과 downgrade를 포함한다.
6. 테스트 통과 후에도 diff를 읽어 code review를 한 뒤에만 merge 후보로 둔다.
7. 리뷰 결과와 잔여 리스크를 보고하고 사용자 승인 후 main merge를 진행한다.

권장 생성 명령:

```bash
git fetch origin
git worktree add ../bid-vector-<slug> -b <type>/<slug> origin/main
```

## 병렬 작업 요약

| 에이전트 | 브랜치 예시 | 주 목표 | 주 쓰기 범위 | 병렬성 |
|---|---|---|---|---|
| A | `feature/g2-evidence-ledger` | 사업자별 G-2 증적 summary/API | `app/services/analytics_reporting.py`, `app/api/analytics.py`, `app/schemas/`, analytics/smoke tests | 운영 증적 도메인 |
| B | `feature/g2-notification-channels` | 사업자별 알림 대상 매핑과 송신 정책 | `app/models/`, `alembic/`, `app/services/notifications/`, `app/api/operator.py`, notification tests | 알림/모델 도메인 |
| C | `feature/g2-synthetic-evidence-runs` | sample-gap 후보를 실행 가능한 증적 run으로 연결 | `app/api/synthetic.py`, `app/services/synthetic_experiment.py`, `scripts/`, synthetic tests | synthetic 도메인 |
| D | `feature/g2-admin-user-surfaces` | 관리자/사용자 화면 경계 강화 | `frontend/src/app/`, `frontend/src/features/operations/`, `frontend/src/features/dashboard/`, frontend tests | 프론트 surface 도메인 |
| E | `docs/g2-evidence-runbook` | G-2 운영 runbook과 체크리스트 | `docs/operations/`, `docs/roadmap.md`, `README.md` | 문서/운영 절차 |

## Agent A: G-2 증적 ledger/API

목표:

- G-2 exit 판단에 필요한 사업자별 증적을 한 응답에서 볼 수 있게 한다.
- smoke, strategy monitor, decision experiment, synthetic experiment, notification evidence를 같은 operator scope로 정규화한다.
- G-0 canonical smoke와 G-2 per-operator evidence가 섞이지 않게 표시한다.

권장 구현:

- `G2EvidenceService` 또는 `analytics_reporting` 내부 helper를 추가한다.
- privileged caller만 cross-operator 조회를 허용하고, 일반 operator는 자기 증적만 본다.
- 응답에는 최소한 다음 필드를 포함한다.
  - `current_operator_id`, `current_operator_username`
  - `window_days`
  - `evidence_status`: `ready`, `insufficient`, `mixed_scope`, `missing`
  - `smoke`, `strategy_monitor`, `decision_experiments`, `synthetic_experiments`, `notifications` summary
  - `blocking_gaps`: G-2 exit gate 기준으로 부족한 항목 목록
- 새 endpoint를 추가한다면 `/api/v1/analytics/g2-evidence` 또는 `/api/v1/operator/dashboard`의 별도 섹션으로 제한한다.

소유 범위:

- `app/services/analytics_reporting.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`
- `docs/api/analytics.md`
- `tests/test_analytics_reporting.py`
- `tests/test_operator_reporting_context_api.py`

검증:

```bash
python -m py_compile app/api/analytics.py app/services/analytics_reporting.py app/schemas/schemas.py
pytest tests/test_analytics_reporting.py tests/test_operator_reporting_context_api.py -q
git diff --check origin/main..HEAD
```

리뷰 포인트:

- `operator_id` 없는 증적을 G-2 ready로 계산하지 않는다.
- synthetic/canonical mixed data는 `ready`가 아니라 gap 또는 warning으로 드러낸다.
- 실제 낙찰 확률처럼 보이는 표현을 쓰지 않는다.

## Agent B: 사업자별 알림 대상 매핑

목표:

- synthetic/non-canonical operator의 실제 Telegram/app 알림 대상이 canonical operator 채널로 섞이지 않게 한다.
- 사업자별 알림 대상 식별자를 모델로 분리하고, active가 아닌 채널은 dry-run evidence로 남긴다.
- callback owner 검증을 유지하면서 operator별 route key를 명확히 한다.

권장 구현:

- 새 모델 후보: `OperatorNotificationChannel`
  - `operator_id`
  - `channel_type`: `telegram`, `app`
  - `route_key` 또는 masked target label
  - 실제 secret/token은 저장하지 않는다.
  - `is_active`, `dry_run_only`, `verified_at`, `created_at`, `updated_at`
- Telegram chat id는 민감 운영정보로 취급한다. 저장이 필요하면 원문 노출을 피하고, API/log에는 masked value만 반환한다.
- synthetic operator는 channel이 없거나 `dry_run_only=true`이면 실제 송신하지 않는다.
- canonical operator의 기존 Telegram 동작은 유지한다.

소유 범위:

- `app/models/models.py`
- `alembic/versions/`
- `app/services/notifications/manager.py`
- `app/services/notifications/telegram.py`
- `app/services/notifications/update_processor.py`
- `app/api/operator.py` 또는 알림 설정용 최소 API
- `tests/test_operations.py`
- 필요 시 `tests/test_operator_context_api.py`

검증:

```bash
python -m py_compile app/api/operator.py app/services/notifications/manager.py app/services/notifications/telegram.py app/services/notifications/update_processor.py
pytest tests/test_operations.py -q
pytest tests/test_operator_context_api.py -q
git diff --check origin/main..HEAD
```

리뷰 포인트:

- 실제 Telegram token/chat id가 테스트 fixture, 문서, 로그에 남지 않는다.
- cross-operator callback이 다른 operator의 decision/notification을 조작하지 못한다.
- 마이그레이션이 기존 SQLite/Postgres 테스트를 깨지 않는다.

## Agent C: sample-gap 기반 synthetic 증적 실행

목표:

- `/synthetic/experiments/sample-gaps/candidates`를 실제 G-1/G-2 증적 run으로 이어지게 한다.
- 운영자가 부족 표본을 보고 수동 해석하지 않아도 반복 가능한 preset 실행 계획을 만들 수 있게 한다.
- 요청-응답 경로에서 무거운 백테스트를 직접 돌리지 않는다.

권장 구현:

- candidate response에서 선택한 action을 기반으로 experiment preset을 생성하거나 기존 preset run 요청 payload를 만든다.
- `run_allowed=false` 또는 mixed data warning이 있으면 실행 대신 정리/재실행 안내를 우선한다.
- CLI 스크립트 후보:
  - `scripts/run_g2_synthetic_evidence.py --dry-run`
  - `scripts/run_g2_synthetic_evidence.py --write --preset <id>`
- `--write`는 실제 DB write이므로 사용자가 승인한 운영 실행에서만 사용한다.
- synthetic run summary에 source sample-gap candidate context를 남긴다.

소유 범위:

- `app/api/synthetic.py`
- `app/services/synthetic_experiment.py`
- `app/schemas/schemas.py`
- `scripts/`
- `docs/api/synthetic.md`
- `tests/test_synthetic_experiment.py`
- `tests/test_synthetic_experiment_breakdown.py`

검증:

```bash
python -m py_compile app/api/synthetic.py app/services/synthetic_experiment.py app/schemas/schemas.py
pytest tests/test_synthetic_experiment.py tests/test_synthetic_experiment_breakdown.py -q
git diff --check origin/main..HEAD
```

리뷰 포인트:

- API 요청 하나로 대량 backfill/write를 바로 실행하지 않는다.
- canonical operator 데이터가 섞인 run을 reporting-ready처럼 보이게 하지 않는다.
- settled sample이 부족한 상태와 실제 성과 검증 완료 상태를 명확히 구분한다.

## Agent D: 관리자/사용자 surface 분리

목표:

- 사용자 화면은 투찰 판단, 알림, 결과 확인에 집중하게 한다.
- 관리자 화면은 G-2 증적, smoke, synthetic experiment, operations dashboard, 데이터 상태를 구분해서 보여준다.
- operator switcher와 cross-operator 조회는 admin surface에만 남긴다.

권장 구현:

- `/dashboard`에는 관리자 전용 지표와 백테스트/스모크 운영 링크를 노출하지 않는다.
- `/admin/operations` 또는 새 admin 섹션에 G-2 evidence summary를 표시한다.
- privileged 사용자는 operator별 evidence/status를 전환해서 볼 수 있고, 일반 사용자는 `/admin/*` 접근 시 `/dashboard`로 이동한다.
- 사용자-facing 문구에서 `probability_score`를 실제 낙찰 확률처럼 표현하지 않는다.

소유 범위:

- `frontend/src/app/router.tsx`
- `frontend/src/app/layout/`
- `frontend/src/shared/api/`
- `frontend/src/shared/types/`
- `frontend/src/features/operations/`
- `frontend/src/features/dashboard/`
- 관련 frontend tests

검증:

```bash
npm --prefix frontend test -- src/app/layout/OperatorSwitcher.test.tsx src/features/operations/OperationsScreen.test.tsx src/features/dashboard/HomeScreen.test.tsx
npm --prefix frontend run build
git diff --check origin/main..HEAD
```

리뷰 포인트:

- 관리자 화면에서만 operator switcher/cross-operator evidence를 보여준다.
- 일반 사용자 화면에 smoke/backtest/statistics/data collection 관리 기능이 노출되지 않는다.
- 반응형 레이아웃에서 텍스트 겹침이나 버튼 overflow가 없다.

## Agent E: G-2 운영 runbook

목표:

- G-2 완료 판단에 필요한 실제 운영 절차와 증적 체크리스트를 문서화한다.
- 각 에이전트의 구현 결과를 운영자가 N일 동안 반복 실행할 수 있는 순서로 연결한다.

필수 문서:

- `docs/operations/g2-evidence-runbook.md`
- 필요 시 `docs/production-smoke-test.md`
- 필요 시 `README.md`
- 필요 시 `docs/roadmap.md`

runbook 포함 항목:

- 3개 이상 가상 사업자 준비 조건
- operator별 프로필/전략/알림 대상 확인 절차
- scheduled smoke, strategy monitor, decision experiment, synthetic experiment 실행 순서
- Telegram/app 알림은 dry-run과 실제 송신을 어떻게 구분하는지
- 1일 단위 evidence checklist
- G-2 exit review 양식
- 실패 원인 분류: credential, KONEPS 응답, 후보 없음, Telegram/app notification, task/broker, mixed data

검증:

```bash
rg -n "G-2|operator_id|synthetic|Telegram|smoke|sample-gap|evidence" docs README.md
git diff --check origin/main..HEAD
```

리뷰 포인트:

- 실제 외부 송신/DB write를 문서상 기본값으로 두지 않는다.
- 완료 조건과 관찰 조건을 혼동하지 않는다.
- 새 계획 문서를 완료된 작업처럼 쓰지 않는다.

## 통합 순서

권장 merge 순서:

1. Agent A: G-2 evidence API/summary
2. Agent B: 알림 대상 매핑 모델/정책
3. Agent C: sample-gap 기반 synthetic 증적 실행
4. Agent D: 관리자/사용자 surface 분리
5. Agent E: runbook과 문서 정리

이유:

- Agent D는 Agent A/B의 응답 형태와 알림 상태를 화면에 연결할 가능성이 높다.
- Agent E는 최종 API/화면 이름을 문서에 반영해야 하므로 마지막이 안전하다.
- Agent B가 모델/migration을 바꾸면 통합 초기에 반영해 충돌을 빨리 드러내야 한다.

## 통합 검증 세트

통합 worktree를 별도로 만들고 각 브랜치를 merge한 뒤 검증한다.

```bash
git worktree add ../bid-vector-integration-g2-exit -b integration/g2-exit origin/main
cd ../bid-vector-integration-g2-exit
git merge --no-ff feature/g2-evidence-ledger
git merge --no-ff feature/g2-notification-channels
git merge --no-ff feature/g2-synthetic-evidence-runs
git merge --no-ff feature/g2-admin-user-surfaces
git merge --no-ff docs/g2-evidence-runbook
```

권장 통합 검증:

```bash
python -m py_compile app/api/analytics.py app/api/operator.py app/api/synthetic.py app/services/analytics_reporting.py app/services/notifications/manager.py app/services/synthetic_experiment.py
pytest tests/test_analytics_reporting.py tests/test_operator_reporting_context_api.py tests/test_operator_context_api.py -q
pytest tests/test_operations.py tests/test_synthetic_experiment.py tests/test_synthetic_experiment_breakdown.py -q
npm --prefix frontend test -- src/app/layout/OperatorSwitcher.test.tsx src/features/operations/OperationsScreen.test.tsx src/features/dashboard/HomeScreen.test.tsx
npm --prefix frontend run build
git diff --check origin/main..HEAD
```

마이그레이션이 포함된 경우 추가 검증:

```bash
alembic upgrade head
```

## 완료 보고 형식

각 에이전트는 작업 완료 시 아래를 보고한다.

- 브랜치/커밋
- G-2 exit gate 중 어떤 조건을 보강했는지
- 변경 파일 요약
- 실행한 검증 명령과 결과
- 실제 외부 호출/DB write 여부
- 남은 gap과 다음 에이전트가 알아야 할 충돌 가능성

통합 담당자는 merge 전 code review에서 blocking issue, non-blocking risk, 테스트 gap을 먼저 보고하고 사용자 승인 후에만 main merge를 진행한다.
