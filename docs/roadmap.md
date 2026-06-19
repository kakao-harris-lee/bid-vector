# bid-vector 로드맵

기준일: 2026-06-19

이 문서는 `bid-vector`의 단계별 목표와 exit gate를 정리하는 단일 로드맵입니다. 오래된 계획 문서보다 현재 코드와 이 문서를 우선합니다.

## 현재 결론

0~1단계의 핵심 빌드는 대부분 완료되어 있습니다. 2단계는 독립 가상 사업자 운영 검증으로 진입했고, G-2 exit 판단을 위한 evidence API, 알림 채널 메타데이터, sample-gap 기반 synthetic evidence 실행 계획, 관리자/사용자 surface 분리, 운영 runbook이 `main`에 반영되었습니다. 현재 병목은 대형 기능 추가가 아니라 **N일 운영 증적 축적, 실제 표본 실행, 사업자별 알림 대상 확인, G-2 exit review**입니다.

현재 검증 환경은 외부 실사용자 SaaS가 아닙니다. 운영자 1명이 가상의 여러 회사를 만들고, 입찰 종류별 추천, 가상 투찰, 정산, 정확도 리포트, smoke test 자동화를 반복하면서 서비스 가능성을 확인하는 단계입니다.

## 핵심 목표

1. 사업자가 나라장터에 등록된 입찰 정보 중 본인 조건에 맞고 입찰 가능한 공고를 놓치지 않게 한다.
2. 공고 확인, 가격 산정, 투찰 여부 판단의 반복 업무를 줄인다.
3. 과거 데이터와 개찰 결과를 학습해 추천 투찰가를 최대한 실제 낙찰가에 가깝게 만든다.
4. Telegram 또는 앱 알림으로 후보 공고를 전달하고, 운영자가 투찰/검토/보류를 선택하게 한다.
5. 실증 후 수수료 또는 구독 기반 서비스 사업으로 확장한다.

## 단계 요약

| 단계 | 이름 | 상태 | 핵심 질문 |
|---|---|---|---|
| 0 | 단일 운영자 검증 기반 | 빌드 완료, 관찰 중 | 실제 키와 스케줄로 매일 깨지지 않는가 |
| 1 | 가상 회사 실험실 | 구현됨, 실행 후보 연결됨, 표본 실행 필요 | 업종/규모별 가상 회사에서 추천 품질이 검증되는가 |
| 2 | 독립 가상 사업자 운영 검증 | 진행 중, 실행/증적 축적 필요 | 각 회사가 독립 ID/사업자 정보로 서비스처럼 운영되는가 |
| 3 | 제한 실증 서비스 | G-2 후 착수 | 실제 사업자가 매일 써도 업무 시간이 줄고 추천이 유효한가 |
| 4 | SaaS/수수료 사업화 | G-3 후 착수 | 과금, 보안, 운영지원까지 견딜 수 있는가 |

## 최근 반영된 작업

2026-06-19 `7fdc04c` 기준으로 다음 G-2 exit 기반 작업이 `main`에 반영되었습니다.

- G-2 evidence ledger: `/api/v1/analytics/g2-evidence`가 operator별 smoke, strategy monitor, decision experiment, synthetic experiment, notification 증적을 `ready`/`insufficient`/`mixed_scope`/`missing` 상태와 `blocking_gaps`로 정리합니다.
- G-2 notification channels: `OperatorNotificationChannel` 모델과 Alembic migration이 추가되었습니다. `/api/v1/operator/notification-channels`는 raw chat id 없이 masked route metadata만 반환합니다. synthetic/non-canonical operator는 채널 없음, inactive, dry-run 상태를 실제 송신 없이 evidence로 남깁니다.
- G-1/G-2 synthetic evidence runs: sample-gap candidate 응답에 `execution_plan`이 추가되었고, `scripts/run_g2_synthetic_evidence.py`는 기본 dry-run으로 계획을 출력하며 승인 후 `--write`로만 DB write/async run enqueue를 수행합니다.
- G-2 admin/user surface: `/admin/operations`는 G-2 증적 요약을 표시하고, `/dashboard`는 token owner 기준 투찰 판단 화면으로 제한됩니다. operator switcher/cross-operator 조회는 admin surface에만 남습니다.
- G-2 runbook: `docs/operations/g2-evidence-runbook.md`가 3개 이상 가상 사업자의 일일 증적 수집, dry-run/승인 후 실행 경계, exit review 양식을 정리합니다.
- 통합 리뷰 수정: operator-scoped smoke evidence가 존재할 때 같은 기간의 canonical/다른 operator smoke 때문에 `mixed_scope`로 떨어지는 문제를 수정했습니다.
- 통합 검증: backend 선택 테스트 201개, frontend 테스트 24개, frontend production build, `py_compile`, schema drift/migration test, `git diff --check`가 통과된 상태로 병합되었습니다.

2026-06-19 `06eeee5` 기준으로 다음 작업이 `main`에 반영되었습니다.

- G-2 API contract sync: `docs/api/operator.md`, `docs/api/analytics.md`, `docs/api/synthetic.md`, `docs/api/index.md`가 monitor/decision experiment/sample-gap/candidates API와 operator target context 규칙을 반영합니다.
- G-2 notification routing isolation: synthetic/non-canonical operator의 Telegram 실제 송신은 skip/dry-run evidence로 남기고, canonical operator 기존 Telegram 동작과 callback owner 검증을 유지합니다. 사업자별 실제 chat/channel 매핑은 아직 후속 작업입니다.
- G-1 sample-gap execution bridge: `/api/v1/synthetic/experiments/sample-gaps/candidates`가 표본 부족 gap을 실행 후보 payload로 변환합니다. 프론트 synthetic experiment lab에서 gap 기반 후보 선택과 preset 저장 흐름을 사용할 수 있습니다. 실제 DB backfill/write 실행은 하지 않습니다.
- G-2 operations evidence isolation: smoke/analytics evidence가 `operator_scope`, `current_operator_id`, `source_run_type`, `source_run_id`를 남겨 G-0 canonical smoke와 G-2 per-operator evidence를 구분할 수 있습니다.
- 통합 검증: backend 선택 테스트 178개, frontend 테스트 12개, frontend production build, `py_compile`, `git diff --check`가 통과된 상태로 병합되었습니다.

이전 2026-06-18 `ab5a6f5` 기준으로 다음 작업이 먼저 `main`에 반영되었습니다.

- G-2 monitor context: `/api/v1/operator/strategy/monitor*` 동기/비동기 실행, run list/detail, task status가 `operator_id` 기준 target operator를 유지합니다. 무인증 cross-operator target은 `403`으로 차단됩니다.
- G-2 decision experiment context: `/api/v1/analytics/decision-experiments*` 생성/목록/상세/평가/전략 적용이 target operator 전략과 실험 run에만 적용됩니다.
- G-2 admin/user boundary: 프론트 `/admin/*` 라우트는 privileged operator만 접근하고, 일반 사용자는 `/dashboard`로 이동합니다.
- G-1 sample gap planning: `/api/v1/synthetic/experiments/sample-gaps`가 최근 completed experiment의 `sample_report.lacking_groups`를 모아 read-only backfill 계획을 반환합니다.
- 통합 검증: backend 115개 테스트, frontend 38개 테스트, frontend production build가 통과된 상태로 병합되었습니다.

## Phase 0. 단일 운영자 검증 기반

목표: 현재 코드가 실제 KONEPS/Telegram 환경에서 매일 한 사이클 돈다는 증적을 남긴다.

이미 구현됨:

- KONEPS 공고/개찰 수집과 `Project`, `HistoricalData`, `TenderResult` 연결
- 가격 예측, guardrail, 의사결정 기록
- Telegram 알림/버튼 callback, `/strategy` 편집
- 운영 smoke test 스크립트와 `SmokeTestRun` 영속화
- operations dashboard, 정확도 리포트, 의사결정 증적 export

해야 할 일:

- `scripts/production_smoke_test.py` read/write 실행 결과를 주기적으로 저장
- KONEPS 수집 성공률, Telegram 전송률, strategy monitor 성공률을 N일 관찰
- 실패 원인을 credential, KONEPS 응답, 후보 없음, Telegram, task/broker로 분류

Exit gate G-0:

- 최소 7일 연속 scheduled smoke 핵심 phase green
- KONEPS 수집, 후보 생성, 알림 또는 알림 생략 사유가 dashboard에 남음
- 실패 시 원인과 재실행 방법이 `docs/production-smoke-test.md`만으로 구분됨

## Phase 1. 가상 회사 실험실

목표: 한 운영자가 여러 가상 회사를 만들어 업종별 입찰 추천, 가상 투찰, 최종 정산을 반복 검증한다.

이미 구현됨:

- `scripts/seed_synthetic_operators.py`
- `scripts/backtest_synthetic_operators.py`
- `app/api/synthetic.py`
- `app/services/synthetic_backtest.py`
- `app/services/synthetic_experiment.py`
- `/api/v1/synthetic/experiments/sample-gaps` read-only 표본 부족/backfill 계획
- `/api/v1/synthetic/experiments/sample-gaps/candidates` 표본 부족 기반 실행 후보 생성
- `frontend/src/features/synthetic-backtest/`

검증 축:

- 공사, 용역, 물품, 소프트웨어 등 입찰 종류별 후보 선별
- 소형/중형/대형 회사별 예산/면허/지역/시공능력 매칭
- historical backtest와 forward paper bidding 비교
- `win_rate_on_settled`, `bid_submission_rate`, 평균 오차율, 분야별 breakdown
- 가격 기준 추정 낙찰과 룰 기반 적격 게이트 추정의 차이

해야 할 일:

- synthetic company catalog를 실제 서비스 검증 목적에 맞게 정리
- sample-gap 후보를 기준으로 업종별 부족 표본을 실제 preset 실행/백필 작업으로 전환
- 동일 기간/동일 전략으로 반복 가능한 experiment preset을 고정
- smoke test 자동화와 synthetic backtest 결과를 같은 운영 리포트에서 비교

Exit gate G-1:

- 주요 입찰 종류별 settled sample이 충분히 쌓임
- 가상 회사별 추천/투찰/정산 결과가 재현 가능한 experiment로 조회됨
- 가격 오차와 추정 낙찰 지표가 category/business group별로 설명 가능함
- canonical operator 데이터와 synthetic 데이터가 섞이지 않음

## Phase 2. 독립 가상 사업자 운영 검증

목표: 가상 회사가 각자의 ID와 사업자 정보를 가지고 실제 서비스 사용자처럼 운영되는지 검증한다.

현재 상태:

- 주요 dashboard/reporting, strategy monitor, decision experiment 경로는 `operator_id` target context와 `current_operator_*` 응답 필드를 지원합니다.
- synthetic operator infrastructure는 있지만 SaaS 멀티테넌트는 아닙니다.
- 현재 프론트는 단일 SPA이지만 `/dashboard` 사용자 surface와 `/admin/*` 관리자 surface의 역할이 분리되었습니다.
- API 문서는 monitor/decision experiment/sample-gap/candidates와 operator target context를 반영합니다.
- `/api/v1/analytics/g2-evidence`가 operator별 G-2 증적 ledger와 blocking gap을 반환합니다.
- synthetic/non-canonical operator Telegram 송신은 dry-run evidence로 남기며, callback owner 검증과 route metadata 분리가 강화되었습니다.
- `OperatorNotificationChannel`은 operator별 masked notification route metadata를 저장하지만, non-canonical 실제 Telegram 송신 secret resolver는 아직 없습니다.
- smoke/analytics evidence는 `operator_scope`, `current_operator_id`, `source_run_type`, `source_run_id`를 남기지만, N일 운영 증적은 아직 충분하지 않습니다.

해야 할 일:

- 운영 DB에 `6f2a8c9d0e12_add_operator_notification_channels.py` migration 적용을 배포 절차에 포함한다.
- 3개 이상 가상 회사별 로그인/프로필/전략/알림/결정 이력을 일일 runbook으로 저장한다.
- `/api/v1/analytics/g2-evidence` 결과를 operator별로 N일 단위 저장하고 `blocking_gaps`를 해소한다.
- `scripts/run_g2_synthetic_evidence.py --dry-run`으로 sample-gap 실행 계획을 검토하고, 승인 후 `--write`로만 synthetic evidence run을 enqueue한다.
- non-canonical 실제 Telegram/app 송신 전에는 operator별 target, masking, secret resolver 정책을 확정한다.
- 사업자 정보와 알림 대상 식별자를 개인정보/운영정보로 취급한다.

Exit gate G-2:

- 3개 이상 가상 사업자가 독립 ID/사업자 정보/전략으로 운영됨
- 각 사업자의 공고 추천과 알림이 서로 섞이지 않음
- 관리자 화면에서 사업자별 백테스트, smoke, 통계, 수집 상태를 구분해 볼 수 있음
- 사용자 화면은 관리 기능 없이 투찰 판단에 집중함

## Phase 3. 제한 실증 서비스

목표: 실제 또는 실제에 준하는 사업자가 매일 사용하면서 업무 절감과 추천 품질을 확인한다.

범위:

- 초기 대상은 공사 입찰 중소업체 중심
- Telegram 알림을 기본 채널로 유지하고, 앱 알림은 다음 채널로 확장
- 자동 투찰 제출 없이 추천, 초안, 상태 기록까지만 제공

해야 할 일:

- 사업자 온보딩: 면허, 지역, 시공능력, 도급한도, 관심/제외 조건
- 알림 품질 조정: 너무 많은 알림을 막고 실제 투찰 가치가 높은 공고만 전달
- 추천가 근거 제공: 예가, 낙찰하한, 유사 공고, 과거 오차, 리스크
- 투찰 선택 기록: 투찰/검토/보류와 사유를 감사 가능하게 저장
- 낙찰/유찰 결과 수집과 추천 품질 피드백 루프 운영

Exit gate G-3:

- 1~3개 사업자가 2~4주 매일 사용
- 공고 검토 시간 감소, 놓친 유효 공고 감소, 추천 검토 가치, 투찰가 오차가 측정됨
- 추천을 믿을 수 없는 상황과 추천 가능한 상황이 지표로 구분됨
- G-3 전에는 SaaS 멀티테넌트 대공사를 시작하지 않음

## Phase 4. SaaS/수수료 사업화

목표: 검증된 추천/알림/투찰 보조 기능을 유료 서비스로 전환한다.

필요 작업:

- 멀티테넌트 데이터 모델과 tenant isolation
- RBAC, 관리자 권한, 감사 로그
- 사업자 온보딩과 사업자 정보 보호
- 요금제/수수료/구독 모델
- 월간 성과 리포트와 세금계산/정산 지원
- 고객 지원용 운영 대시보드
- KONEPS 호출량, Telegram/app 알림량, ML 비용 통제

초기 상품 가설:

- Lite: 조건 기반 공고 추천/알림
- Pro: 투찰가 추천, 근거, 투찰서 초안, 결과 추적
- Expert: 백테스트, 전략 튜닝, 월간 성과 리포트, 컨설팅

## 교차 워크스트림

### 데이터

- KONEPS 공고/개찰 수집 안정성
- notice number canonicalization
- `TenderResult`와 `Project` 정합
- historical/forward paper settlement coverage

### 추천 품질

- 가격 예측 오차
- category/business group별 guardrail
- 지역/면허/시공능력/도급한도 매칭
- 추천 피드백 label과 threshold tuning

### 운영 자동화

- scheduled smoke
- synthetic experiment preset
- operations dashboard
- ML release preflight
- worker/beat 재시작 검증

### 제품/사업

- 사용자 웹과 관리자 웹 분리
- 알림 피로도 관리
- 투찰 선택 UX
- 리포트와 과금 근거
- 개인정보/사업자정보 보호

## 다음 우선순위

1. G-2 운영 증적 축적: 3개 이상 가상 사업자별 profile, strategy, notification channel, strategy monitor, decision experiment, synthetic experiment, G-2 evidence ledger를 N일 단위로 저장한다.
2. G-2 blocking gap 해소: `/api/v1/analytics/g2-evidence`의 `blocking_gaps`를 operator별 TODO로 관리하고 `mixed_scope`/`missing` 상태를 제거한다.
3. G-1 표본 실행: sample-gap candidates를 dry-run으로 검토하고 승인 후 synthetic evidence run을 enqueue하여 settled sample 증적을 쌓는다.
4. G-2 알림 대상 검증: 사업자별 Telegram/app notification 대상 식별자, `dry_run_only`, masking, 실제 송신 가능 범위를 운영표로 관리한다.
5. G-0 관찰: scheduled smoke 핵심 phase green을 7일 이상 확보하고 실패 원인을 dashboard와 문서만으로 구분한다.
6. G-2 exit review: `docs/operations/g2-evidence-runbook.md`의 양식으로 3개 이상 operator가 exit gate를 만족하는지 판정한다.
7. G-3 전까지 SaaS 멀티테넌트 전체 전환은 보류한다.

## 관련 문서

- `README.md`: 현재 시스템 개요와 실행 방법
- `CLAUDE.md`: 에이전트 작업 지침
- `docs/operations/g2-exit-agent-plan.md`: G-2 exit 기반 병렬 작업 완료 기록과 잔여 TODO
- `docs/operations/g2-evidence-runbook.md`: 3개 이상 가상 사업자의 G-2 evidence를 N일 단위로 반복 실행하고 exit review를 남기는 운영 절차
- `docs/operations/roadmap-next-agent-plan.md`: 최근 완료된 병렬 작업 기록과 후속 gap
- `docs/production-smoke-test.md`: 운영 smoke test 절차
- `docs/api/index.md`: HTTP API 레퍼런스
