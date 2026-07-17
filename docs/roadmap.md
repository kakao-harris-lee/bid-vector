# bid-vector 로드맵

기준일: 2026-07-03

이 문서는 `bid-vector`의 단계별 목표와 exit gate를 정리하는 단일 로드맵입니다. 오래된 계획 문서보다 현재 코드와 이 문서를 우선합니다.

## 현재 결론

0~1단계의 핵심 빌드는 대부분 완료되어 있습니다. 2단계는 독립 가상 사업자 운영 검증으로 진입했고, G-2 exit 판단을 위한 evidence API, 알림 채널 메타데이터, sample-gap 기반 synthetic evidence 실행 계획, **관리자/사용자 웹 물리 분리(별도 Vite 번들)**, 운영 runbook, operator-scoped synthetic evidence, read-only evidence 수집/스냅샷 경로가 `main`에 반영되었습니다. 2026-06-22 기준으로 **G-0 smoke가 실제 스케줄에서 5 phase 전부 green으로 실증**됐고(smoke ML phase fix #106), **G-2 라이브 증적 축적이 시작**됐으며(사업자별 strategy monitor 실행 + dry-run 알림채널 + 일일 후보 재확인 자동화), 운영 안정화(celerybeat 복구, KST 스케줄 정합, monitor run 고아 정리/reconciler)도 반영됐습니다. 2026-06-24 기준으로 synthetic experiment 결과는 `operator_id`가 붙어야 G-2 ledger에 집계되고, 일일 evidence snapshot은 `reports/g2-evidence/` 파일 수집과 `collect_g2_evidence` analytics event로 축적할 수 있습니다.

2026-07-03 기준으로 개발 노트북에서 처리 가능한 G-2 검증 하드닝, OpenAPI 타입 동기화 가드, 추천 투찰가 guardrail/holdout 백테스트, 세부 조달 세그먼트 밴드와 10원 단위 보정도 `main`에 반영되었습니다. 현재 운영 병목은 대형 기능 추가가 아니라 **N일 운영 증적 축적, 실제 표본 실행(대부분 synthetic 사업자는 좁은 niche × 얇은 입찰가능 재고로 후보가 thin함 — 재고 누적 대기), 사업자별 알림 대상 확인, G-2 exit review**입니다. 추천 품질 쪽은 최신 낙찰 holdout 개선이 들어왔지만, 다음 개발 항목은 `procurement_rate_band`보다 세밀한 feature extractor, selector 분리, legal floor/분모 품질 강화입니다.

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

2026-07-03 (`7aa4e6f` 포함)로 다음이 `main`에 반영되었습니다.

- 조달 세그먼트별 투찰가 예측 개선: 서비스/물품을 `service_price_competitive`, `service_direct_negotiated`, `service_high_negotiated`, `goods_price_competitive`, `goods_deep_discount` 등 더 세밀한 `procurement_rate_band`로 나누고, 해양/엔지니어링 가격경쟁형 용역, 수의시담, 물품 견적/2단계 구매 세그먼트의 추천률 왜곡을 줄였습니다.
- 최종 투찰가 단위 보정: 추천 후보 금액을 기본 10원 단위로 내림 처리하고, 낙찰하한 안전가격을 침범하면 올림 보정합니다. API 응답과 OpenAPI 타입에는 `bid_price_granularity`, `bid_price_rounding_mode`, `price_granularity_applied`, `pre_granularity_price`가 반영되었습니다.
- 최신 낙찰 holdout 확장 검증: 2026-07-02 3건 기준선은 추천 평균 절대오차율 3.559%였고, 세그먼트/금액단위 개선 후 0.5545%로 낮아졌습니다. 업무구분별 150건 확장 holdout에서는 clean 표본 141건 기준 추천 평균 절대오차율 1.837%, 1.0% 이내 76/141로 기록되었습니다.
- 추천 품질 후속 문서화: `docs/operations/latest-award-holdout-backtest.md`와 `docs/operations/procurement-segment-improvement-notes.md`에 개선 전후 수치, 데이터 품질 플래그, 다음 세그먼트 개선 축을 고정했습니다.

2026-07-02 (`16f2f58`, `1e111fe`, `81450e5` 포함)로 다음이 `main`에 반영되었습니다.

- 가격 예측 guardrail과 holdout 백테스트: 공고별 법정 하한(`legal_floor_bid_rate`)과 safety margin을 추천 후보에 반영하고, 업무구분별 최신 낙찰결과 holdout을 `scripts/backtest_latest_award_holdouts.py`로 재현할 수 있게 했습니다. ML release business group 문서와 scheduler/test coverage도 갱신되었습니다.
- OpenAPI 타입 동기화 가드: `scripts/sync_openapi_types.py`, `npm --prefix frontend run sync-types`, `npm --prefix frontend run check:sync-types`가 추가되어 API 스키마 변경 후 `frontend/src/shared/types/openapi.d.ts`를 갱신/검증합니다.
- 개발 노트북용 G-2 검증 하드닝: 관리자 홈에서 사용자 화면 메뉴가 추가 노출되지 않는지 회귀 테스트를 추가했고, sample-gap dry-run 화면은 write safety/status를 명시합니다. notification target verifier는 nested metadata/target context의 raw secret-like target도 검사합니다.
- G-2 exit review 기준 강화: `scripts/build_g2_exit_review.py`, `scripts/check_g2_exit_readiness.py`, `scripts/g2_blocking_gap_register.py`는 `open`, `triaged`, `accepted_hold` gap을 모두 unresolved로 취급합니다. `resolved` 또는 `excluded`만 남아야 exit 근거로 넘길 수 있습니다.

2026-06-24 (`50c9336` 포함, PR #113~#116)로 다음이 `main`에 반영되었습니다.

- decision analytics 시간 정합(#113): `entry_timestamp` 생성에서 naive/aware datetime 혼합을 제거해 decision analytics 응답/테스트가 시간대 처리에 흔들리지 않도록 수정.
- synthetic experiment operator scope(#114): synthetic experiment 결과 저장 시 upstream `user_id`를 `operator_id`로 mirror해 G-2 ledger가 operator별 synthetic evidence를 집계할 수 있게 수정. 기존 slug-only 결과는 `mixed_scope`로 남기며, G-2 성공 근거로 쓰려면 operator_id-scoped로 재실행 또는 보정해야 함.
- 일일 G-2 evidence snapshot 자동화(#115): `COLLECT_G2_EVIDENCE_*` 설정과 `jobs.collect_g2_evidence` beat task가 추가됨. 기본 OFF이며, 활성화 시 22:00 KST에 canonical + active synthetic operator의 `/analytics/g2-evidence` 요약을 한 개의 `collect_g2_evidence` analytics event로 저장한다. strategy monitor 실행, operator 데이터 write, 외부 호출, Telegram 송신은 하지 않는다.
- evidence 경로 정합(#116): G-2 exit review 증적 위치는 `.gitignore`와 smoke-evidence convention에 맞춰 `reports/g2-evidence/...`를 사용한다. `models/reports/...`는 더 이상 새 문서/manifest의 기준 경로가 아니다.

2026-06-22 (`88d9f53` 포함, PR #106~#111)로 다음이 `main`에 반영·배포되었습니다.

- G-0 smoke 실증(#106): smoke ML phase가 "최근 30분 내 신규 공고"(smoke가 공고를 persist하지 않아 구조적으로 항상 비어 FAIL)가 아니라 "최근 7일 내 비-fallback 임베딩 + budget 우선 실제 공고"를 평가하도록 수정. **06-22 07:00 KST 스케줄 smoke가 5 phase 전부 green**으로 실증(이전 06-16 2회 FAIL).
- monitor 스캔 정확성/성능(#108): strategy monitor 후보 스캔이 만료(과거 마감) 공고를 제외하고 **입찰가능 공고만** 평가(`deadline IS NULL OR deadline > now()`). 만료 공고에 ML 평가 낭비 + 미래 후보 누락을 차단.
- 스케줄 시각 KST 정합(#109): celery가 이미 `timezone=Asia/Seoul`인데 설정명/주석이 UTC였던 misnomer를 `*_HOUR_KST`로 정정. **smoke 07:00 KST, G-2 일일 재확인 21:00 KST**.
- G-2 일일 후보 재확인 자동화(#107): read-only `preview_candidates` 스윕으로 사업자별 입찰가능 후보 수를 매일 측정해 analytics 증적(`g2_candidate_recheck`)으로 기록(operator 데이터 무변경). niche 재고 회복 추적용, `.env`로 ON.
- 사용자/관리자 웹 물리 분리(#110): 단일 SPA를 `/dashboard`(사용자)·`/admin`(관리자) **두 독립 Vite 번들**로 분리(BUILD_TARGET 이중 빌드). 사용자 앱에 admin 화면/fetch 코드 미포함, FastAPI가 각 번들을 별도 서빙. 앱 경계 이동은 full-page.
- monitor run 수명주기/고아 정리(#111): 고아 `running` strategy_run/crawl_job이 누적돼 operations `task_stale_queue`를 critical로 만들던 문제를, finalize-on-failure(예외 mask 없음, 새 세션 폴백) + 주기 stale-task reconciler(하드리밋+grace 초과 비종료 row를 failed로 마감)로 차단. reconciler ON. **monitor 스케줄 자체는 perf 재검토 전까지 OFF**.
- 운영 안정화: celerybeat 스케줄 corruption(`_dbm.error`) 크래시 루프 복구(주기 작업 재가동). 고아 monitor run 10건 수동 failed 마감.
- G-2 라이브 증적 kickoff: synthetic 사업자별 strategy monitor 실행(op14 sw-small 1후보→결정→in-app 알림 / op19 gs-cleaning 12후보) + op14/19/21/24 dry-run 알림채널 생성. **대부분 사업자는 좁은 niche × 얇은 입찰가능 재고로 후보 0** — 재고 누적 대기(데이터 부재이지 설정/버그 아님). canonical operator만 Telegram 실송신, synthetic은 구조적 skip/dry-run.

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
- 프론트는 `/dashboard`(사용자)와 `/admin`(관리자)이 **두 독립 Vite 번들로 물리 분리**되었습니다(#110). 사용자 앱에 admin 코드 미포함, 관리자 번들은 privileged operator 가드. 완전한 모노레포 분리·RBAC는 후속(Phase 3/4).
- API 문서는 monitor/decision experiment/sample-gap/candidates와 operator target context를 반영합니다.
- `/api/v1/analytics/g2-evidence`가 operator별 G-2 증적 ledger와 blocking gap을 반환합니다.
- `scripts/collect_g2_evidence.py`가 operator 3개 이상에 대한 read-only HTTP evidence 파일을 `reports/g2-evidence/` 아래에 저장합니다.
- `COLLECT_G2_EVIDENCE_*` beat task가 매일 22:00 KST에 operator별 G-2 evidence 요약을 하나의 analytics event로 snapshot할 수 있습니다. 기본값은 OFF입니다.
- synthetic/non-canonical operator Telegram 송신은 dry-run evidence로 남기며, callback owner 검증과 route metadata 분리가 강화되었습니다.
- `OperatorNotificationChannel`은 operator별 masked notification route metadata를 저장하지만, non-canonical 실제 Telegram 송신 secret resolver는 아직 없습니다.
- synthetic experiment 결과는 `operator_id`가 있어야 G-2 operator evidence로 집계됩니다. slug-only 결과는 `mixed_scope`로 분류됩니다.
- G-2 exit review builder/readiness checker/gap register는 `open`, `triaged`, `accepted_hold` gap을 모두 unresolved로 다룹니다. `resolved` 또는 `excluded`만 남아야 G-2 성공 근거로 넘길 수 있습니다.
- notification target verifier는 operator별 `notification-channels.json`의 nested metadata/target context까지 raw secret-like target을 검사합니다.
- sample-gap 기반 실행 화면과 CLI dry-run은 write safety/status를 증적으로 남깁니다.
- smoke/analytics evidence는 `operator_scope`, `current_operator_id`, `source_run_type`, `source_run_id`를 남기지만, N일 운영 증적은 아직 충분하지 않습니다.

해야 할 일:

- 운영 DB에 `6f2a8c9d0e12_add_operator_notification_channels.py` migration 적용을 배포 절차에 포함한다.
- 3개 이상 가상 회사별 로그인/프로필/전략/알림/결정 이력을 `reports/g2-evidence/` 아래에 일일 runbook으로 저장한다.
- `/api/v1/analytics/g2-evidence` 결과를 operator별로 N일 단위 저장하거나 `COLLECT_G2_EVIDENCE_*` snapshot으로 축적하고 `blocking_gaps`를 해소한다.
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

- Phase 3 최우선 실증 대상은 **공고문에 엔지니어링협회 가입 또는 엔지니어링사업자/기술용역 자격 조건이 명시된 공고**와, 해당 조건을 실제로 충족하는 **엔지니어링협회 가입 회사**다. 일반 공사 입찰 중소업체 실증은 이 세그먼트 이후로 둔다.
- Telegram 알림을 기본 채널로 유지하고, 앱 알림은 다음 채널로 확장
- 자동 투찰 제출 없이 추천, 초안, 상태 기록까지만 제공
- 사업자번호 기반 반자동 온보딩은 상태/진위 확인, 프로필 후보 자동입력, 사용자 확정, 공고 미리보기까지로 제한한다. 사업자번호만으로 면허·지역·시공능력·관심 공고 조건을 확정하지 않는다.
- 투찰 보고서 메일 전달은 `BidSummaryService`/`BidFormDraftService` 산출물을 재사용하는 읽기 전용 전달 채널로 제한한다. 나라장터 자동 제출, 수신자 원문 노출, 승인 없는 자동 발송은 범위 밖이다.

해야 할 일:

- 사업자 온보딩: 면허, 지역, 시공능력, 도급한도, 관심/제외 조건
- 엔지니어링협회 실증 cohort 정의: `docs/marine-engineering-gate.md`의 해양/항만 엔지니어링 게이트를 출발점으로 삼되, 실제 회사별 협회 가입 여부, 보유 기술부문/전문분야, 수행 가능 지역, 참여 가능한 금액대를 사용자가 확정한 값으로 저장한다.
- 엔지니어링협회 조건 공고 식별: 공고 제목/본문/면허제한/참가자격에서 `엔지니어링협회`, `엔지니어링사업자`, `엔지니어링 활동주체`, 기술부문/전문분야, 항만·해안·해양·수로조사 관련 조건을 구조화 feature로 추출한다. 단순 키워드 hit가 아니라 공고가 요구하는 자격 조건인지, 기관명/일반 설명/과업명인지 라벨을 분리한다.
- ML 실증 데이터셋: 엔지니어링협회 조건 명시 공고를 positive set으로 고정하고, 해양/항만 키워드는 있으나 협회 가입 조건이 없는 공고, 기관명 때문에 오탐되는 공고, 일반 기술용역 공고를 negative/ambiguous set으로 분리한다. precision/recall, 후보 없음 원인, 추천 투찰가 오차를 실증 리포트에 별도 집계한다.
- 사업자번호 기반 프로필 보조: 국세청 사업자등록정보 API로 휴폐업/과세유형/진위 확인을 먼저 수행하고, 외부 조회 또는 내부 규칙으로 추론한 면허·지역·업종·금액 범위 후보는 사용자가 확정해야 `CompanyProfile`/`OperatorStrategy`에 반영한다.
- 온보딩 직후 후보 확인: 확정된 프로필/전략으로 `/api/v1/operator/strategy/candidates`를 실행해 "현재 이 사업자에게 맞는 공고"를 즉시 보여주고, 후보가 없으면 어떤 입력값이 과하게 좁혔는지 설명한다.
- 알림 품질 조정: 너무 많은 알림을 막고 실제 투찰 가치가 높은 공고만 전달
- 투찰 보고서 메일 전달: 사용자 화면의 "메일로 보내기" 수동 액션을 먼저 구현하고, 메일 본문에는 투찰 요약 HTML/텍스트를, 첨부 또는 본문 하단에는 투찰서 초안 CSV/text를 포함한다. 전송 서비스는 `EmailNotificationService`로 분리하고, `OperatorNotificationChannel.channel_type = "email"`로 operator별 route metadata, masked target, `dry_run_only`, verified state를 관리한다.
- 메일 전송 증적: SMTP/메일 provider 설정은 `Settings`에 명시적으로 추가하고, 발송 성공/실패는 `Analytics` 또는 delivery log에 남긴다. 전송 실패는 보고서 생성/조회 흐름을 막지 않는 best-effort로 처리하며, 자동 발송은 고우선순위 `bid_now` 검증 이후 opt-in으로만 확장한다.
- 추천가 근거 제공: 예가, 낙찰하한, 유사 공고, 과거 오차, 리스크
- 투찰 선택 기록: 투찰/검토/보류와 사유를 감사 가능하게 저장
- 낙찰/유찰 결과 수집과 추천 품질 피드백 루프 운영

Exit gate G-3:

- 1~3개 사업자가 2~4주 매일 사용
- 최소 1개 엔지니어링협회 가입 회사가 협회 가입/기술부문/전문분야 조건이 명시된 공고 추천을 매일 검토하고, 적합/부적합/보류 피드백을 남김
- ML 리포트가 엔지니어링협회 조건 공고의 식별 precision/recall, 오탐 원인, 추천 투찰가 오차, 후보 없음 원인을 별도 세그먼트로 제공함
- 사업자번호 온보딩에서 자동 확인값, 추론 후보, 사용자가 확정/수정한 값이 구분되어 감사 가능하게 남음
- 투찰 보고서 메일 전달은 operator 소유권, 수신자 masking, `dry_run_only`/실전 발송 구분, 성공/실패 telemetry가 확인됨
- 공고 검토 시간 감소, 놓친 유효 공고 감소, 추천 검토 가치, 투찰가 오차가 측정됨
- 추천을 믿을 수 없는 상황과 추천 가능한 상황이 지표로 구분됨
- G-3 전에는 SaaS 멀티테넌트 대공사를 시작하지 않음

## Phase 4. SaaS/수수료 사업화

목표: 검증된 추천/알림/투찰 보조 기능을 유료 서비스로 전환한다. SaaS 단계의 핵심은 모델 추가보다 **사용자 입찰 업무 UX**, **투찰 진행 상태 추적**, **admin 운영 관측성**, **성과/승률/정산 체계**를 제품화하는 것이다.

필요 작업:

- 멀티테넌트 데이터 모델과 tenant isolation
- RBAC, 관리자 권한, 감사 로그
- 사업자 온보딩과 사업자 정보 보호
- 요금제/수수료/구독 모델
- 월간 성과 리포트와 세금계산/정산 지원
- 고객 지원용 운영 대시보드
- KONEPS 호출량, Telegram/app 알림량, ML 비용 통제

SaaS 세부 설계 축:

- 사용자 입찰 워크스페이스: 사업자 온보딩 이후 "오늘 확인할 공고" → 공고 상세/자격 적합성 → 투찰가 요청 → 투찰 보고서 확인 → 메일 공유 → 사용자 결정 → 나라장터 제출 여부 기록 → 개찰 결과 확인까지 끊기지 않는 흐름을 제공한다.
- 설명 가능한 인터랙션: 추천 공고마다 추천 사유, 충족/미충족 조건, 추천 투찰가 근거, 낙찰하한/예가/과거 오차/유사 공고, 사용자가 조정할 수 있는 가정을 함께 보여준다. 사용자의 적합/부적합/보류/투찰 사유는 학습/운영 피드백으로 남긴다.
- Bid lifecycle state machine: `discovered -> matched -> notified -> viewed -> price_requested -> report_generated -> report_sent -> user_decision -> submitted_external -> opened -> awarded/lost -> fee_pending -> invoiced/paid/disputed` 상태를 명시적으로 관리한다. 자동 나라장터 제출은 범위 밖으로 두고, 외부 제출 사실과 증빙을 사용자가 확인/기록하는 방식으로 시작한다.
- Admin 운영 관측성: `/admin`에서 tenant/operator별 공고 수집, 매칭, 알림, 보고서 생성, 메일 전송, 사용자 클릭/결정, 제출 확인, 개찰 결과, 수수료 상태를 한 타임라인으로 추적한다.
- Admin 예외 큐: 후보 없음, 자격 조건 불확실, 마감 임박 미확인, 추천 신뢰도 낮음, 메일/알림 실패, 개찰 결과 매칭 실패, 수수료 분쟁을 운영자가 처리할 수 있는 큐로 분리한다.
- 승률/성과 분석: 전체 낙찰률 하나가 아니라 투찰 대비 낙찰률, 추천 보고서 수락률, 추천가 오차, 세그먼트별 win rate, 모델 버전별 성과, 엔지니어링협회 조건 공고 precision/recall, 사용자 거절 사유를 함께 집계한다.
- 낙찰/수수료 정산: 낙찰 확정, 계약금액, 취소/무효, 세금계산서, 지급기한, 지급 상태, 이의제기/분쟁 상태를 별도 settlement lifecycle로 관리한다. 성공보수형 모델은 계약/법무/세무 검토 전까지 운영 실험 가설로만 둔다.
- 고객지원/감사: support view, read-only impersonation, operator/tenant별 audit log, 개인정보/사업자정보 masking, 관리자 조회 기록을 갖춰 사용자가 "왜 이 공고/가격/수수료가 나왔는지" 문의했을 때 재현 가능하게 한다.
- 비용/품질 통제: tenant별 KONEPS/API 호출량, 알림량, 메일 발송량, ML inference 비용, 보고서 생성 실패율, stale data 비율을 admin에서 추적하고 요금제 한도와 연결한다.

시장 관찰 기준:

- 국내 입찰 정보/분석 서비스는 맞춤 공고 알림, 낙찰/투찰 분석, 전용 투찰함/일정관리, 1:1 분석 컨설팅, 결제/계산서 지원을 핵심 상품으로 묶는다.
- 일부 서비스는 구독형 정보 제공과 전문가 분석을 분리하고, 1:1 분석에는 낙찰금액 비율 기반 성공보수 모델을 붙인다. `bid-vector`는 먼저 투명한 보고서/상태 추적/성과 지표를 제품 차별점으로 삼고, 성공보수는 정산 증빙과 법무/세무 조건이 정리된 뒤 적용한다.
- 해외 tender SaaS는 사업자 프로필 기반 opportunity matching, email notification, amendment 알림, market intelligence, partnership discovery를 기본 UX로 둔다. Phase 4 UX는 "공고 검색"이 아니라 "사업자에게 필요한 다음 행동" 중심으로 설계한다.

초기 상품 가설:

- Lite: 조건 기반 공고 추천/알림
- Pro: 투찰가 추천, 근거, 투찰 보고서/메일 전달, 투찰서 초안, 결과 추적, 기본 승률 리포트
- Expert: 백테스트, 전략 튜닝, 월간 성과 리포트, admin 지원 큐 우선 처리, 컨설팅

## 교차 워크스트림

### 데이터

- KONEPS 공고/개찰 수집 안정성
- notice number canonicalization
- `TenderResult`와 `Project` 정합
- historical/forward paper settlement coverage
- Supabase Pro 기반 원격 데이터 접근: 구조화 데이터는 Supabase Postgres, 모델/학습 산출물은 Supabase Storage 또는 S3 호환 object storage로 분리
- pgvector/vector extension, Alembic migration, 학습/운영 권한 분리, Storage release manifest/checksum 정책

### 추천 품질

- 가격 예측 오차
- category/business group별 guardrail
- Phase 3 최우선 ML 세그먼트: 엔지니어링협회 가입 조건이 명시된 공고와 엔지니어링협회 가입 실제 회사 매칭. 공고 자격 조건, 협회/기술부문/전문분야 표현 위치, 면허제한, 해양·항만·수로조사 키워드, 기관명 오탐 여부를 분리해 feature와 label로 관리
- 업무구분보다 세밀한 조달 세그먼트 분류: 계약/평가 방식, 제목/본문 표현 위치, 키워드 조합,
  금액대, 기관 습관, 법정 하한, 데이터 품질 플래그를 함께 사용
- 가격 레짐 feature layer: 관공서/공공기관/민간, 업태/면허, 공사/용역/물품 세부구분, 계약/평가 방식,
  예정가격 분모, 낙찰하한, 복수예가 맥락을 구조화하고 `floor_bound`, `near_100`, `deep_discount`,
  `ambiguous` 레짐으로 분리
- 과적합 방지 검증: random split 평균이 아니라 최신 N건 rolling holdout, 기관/수요처 group holdout,
  세그먼트별 worst case, 데이터 품질 flag 표본 분리로 평가
- 지역/면허/시공능력/도급한도 매칭
- 추천 피드백 label과 threshold tuning

## 추천 품질 후속 로드맵

2026-07-02 백테스트에서 업무구분(`construction`, `service`, `goods`)만으로는 투찰가 예측을
안정적으로 설명하기 어렵다는 점이 확인됐다. 다음 개선은 `docs/operations/procurement-segment-improvement-notes.md`의
세그먼트 체크리스트를 기준으로 진행한다.

우선 개선 과제:

1. 조달 세그먼트 feature extractor 고도화: 공고 제목 첫 줄, 본문, 계약방식 문구를 분리해
   `procurement_rate_band`보다 넓은 구조화 feature를 만든다. 최소 축은 계약/평가 방식, 표현 위치,
   키워드 조합, 금액대, 기관/수요처, 법정 하한/예정가격 분모, 데이터 품질 플래그다.
2. 키워드 규칙 관리 체계화: positive keyword만 추가하지 말고 negative keyword, 결합 조건,
   제목 전용 조건, 본문 안내문 제외 조건을 함께 기록한다. `관급자재`, `구매 및 설치`, `계측제어`처럼
   고율/저율이 섞이는 표현은 단독 신호로 쓰지 않는다.
3. 세그먼트별 학습/보정: ML 학습과 통계 보정에서 업무구분뿐 아니라 세그먼트별 calibration,
   금액대별 bucket, 기관별 최근 분포를 사용할 수 있게 한다. 표본이 부족한 세그먼트는 전역 모델보다
   낮은 가중치 또는 fallback을 명시한다.
4. recommended 후보 선택 정책 분리: 후보 생성과 최종 추천 선택을 분리한다. 세그먼트별로
   `conservative`/`base`/`aggressive` 중 어떤 후보를 추천으로 승격할지 backtest 기준으로 결정한다.
   `closest`가 `recommended`보다 크게 좋은 구간을 우선 대상으로 삼는다.
5. 법정 하한과 분모 품질 강화: `legal_floor_bid_rate`, 예정가격/기초금액/추정가격 분모 정합,
   `winning_rate`와 `winning_amount / base_amount` 불일치를 학습 전처리와 백테스트 리포트에 반영한다.
6. 세그먼트 회귀 백테스트 확대: 최신 N건 holdout, 해양/엔지니어링 고정 20건, 업무구분별 150건,
   세그먼트별 worst case를 개선 전후 같은 명령으로 비교한다. 결과는 운영 문서에 고정 JSON 경로와
   함께 기록한다.
7. 엔지니어링협회 실증 세그먼트 고정: `docs/marine-engineering-gate.md`의 해양/항만 엔지니어링 게이트를
   Phase 3 첫 실증 세그먼트로 승격한다. ML 작업자는 협회 가입 조건 명시 공고를 positive label로,
   해양/항만 키워드만 있는 공고와 기관명 오탐 공고를 negative/ambiguous label로 분리해 feature extractor,
   candidate selector, 투찰가 calibration을 평가한다.
8. 가격 레짐 feature layer 신설: 낙찰가를 직접 맞추기 전에 공고가 어떤 가격 결정 메커니즘인지 먼저
   구조화한다. 최소 필드는 `buyer_sector`, `buyer_type`, `notice_category`, `business_type_code`,
   `construction_or_service_type`, `contract_method`, `award_method`, `evaluation_method`,
   `price_submission_mode`, `denominator_type`, `legal_floor_bid_rate`, `reserve_price_context`,
   `amount_bucket`, `agency_recent_rate_profile`, `data_quality_flags`로 둔다.
9. 가격 레짐 라벨 정의: feature extractor는 아래 레짐 중 하나와 confidence를 반환한다.
   - `floor_bound`: 적격심사/가격경쟁형처럼 낙찰하한 바로 위가 경쟁선인 공고
   - `near_100`: 협상, 수의시담, 위탁/운영, 단독공급, 유지관리처럼 95~100% 근처가 자연스러운 공고
   - `deep_discount`: 보험, 차량, 2단계, 규격·가격분리, 일부 물품 견적처럼 낮은 낙찰률이 가능한 공고
   - `ambiguous`: 계약방식, 분모, 본문/제목 신호가 충돌해 단일 추천보다 후보 범위와 검토 사유가 필요한 공고
10. 레짐별 예측 target 분리: ML은 모든 공고의 낙찰가를 하나의 target으로 직접 예측하지 않는다.
    `floor_bound`는 `legal_floor_bid_rate + bp_delta`, `near_100`은 `1.0 - discount_rate`,
    `deep_discount`는 세그먼트 분위수 또는 rate bucket, `ambiguous`는 단일값 대신
    `conservative/base/aggressive` 후보와 불확실성으로 평가한다.
11. 고카디널리티 feature 과적합 방지: 발주기관/수요기관/업체명/공고명 n-gram은 raw memorization을
    금지한다. 기관별 최근 낙찰률은 최소 표본 수, time cutoff, 전역/세그먼트 prior shrinkage를 통과한
    경우에만 사용하고, target encoding이 필요하면 cross-fitting 또는 holdout encoding으로 누수를 막는다.
12. 검증 split 정책 고정: 가격 레짐/selector 변경은 random split 평균으로 승인하지 않는다. 필수 검증은
    최신 N건 rolling holdout, 기관/수요처 group holdout, 해양/엔지니어링 고정 20건, 업무구분별 wide holdout,
    레짐별 worst-case replay, `data_quality_flags`별 clean/flag 분리 리포트다.
13. 보고서/UX 노출: 사용자 투찰 보고서와 admin 화면에는 추천가만 노출하지 않고 가격 레짐, 레짐 confidence,
    적용된 낙찰하한, 예정가격/기초금액/추정가격 분모, 기관 표본 수, 데이터 품질 flag,
    `recommended_selector_reason`을 함께 보여준다. `ambiguous`는 자동 단일 추천보다 검토 필요 상태를 우선한다.
14. 릴리스 gate: `price_regime_features` schema, extractor unit test, 레짐별 confusion/worst-case 리포트,
    추천 후보 selector 회귀 비교, OpenAPI 타입 동기화, 최신 낙찰 holdout이 모두 남아야 운영 후보로 본다.
15. competitiveness predictor 재설계 (별도 제안, 2026-07-17 정리 — G-3 관찰 종료 후 착수):
    - 현행: `app/ai/bid_recommendation.py::calculate_competitiveness_score`가 추천가/market_avg 비율의
      4버킷 계단값(0.95/0.75/0.50/0.25, 경계 0.8/1.0/1.2)을 반환한다. `market_avg`는 두 분기 모두
      실제 경쟁사/낙찰 투찰가를 보지 않는다: `market_insights.average_bid`는 유사 공고들의
      추정가격(`budget_estimate`) 평균이고, 부재 시 해당 공고의 추정가격(`budget_estimate`)으로
      폴백한다. 즉 실질적으로 "추천가 vs 유사 공고 추정가격" 비율 지표다. 추가로 PR#162 이후
      분자(`recommended_amount`)는 사업금액(`base_amount`) base인데 분모는 추정가격이라 과세 공고에서
      ~10%(VAT) 체계적 base 불일치가 있다.
      소비 경로: `opportunity_analysis._compute_scores` → `market_insights.competitiveness_score` →
      probability blend(가중치 0.18)·expected_margin_score → `BidDecisionRecord.competitiveness_score`
      persist → bid_summary → BidSummaryScreen "시장 경쟁력" 사용자 노출.
    - 문제: (a) 4단 계단값이 % 점수처럼 노출되어 연속 지표로 오인될 소지(정직 명세 관점),
      (b) 어느 경로에서도 실제 시장 투찰 분포를 반영하지 않아 "시장 경쟁력"이라는 라벨과 실체가
      불일치, (c) 분자/분모 base 불일치(사업금액 vs 추정가격), (d) predictor가 이미 보유한
      이력 낙찰 분포·price_range·투찰가 메뉴 정보를 활용하지 않음.
    - 제안(권장안): 이력 낙찰가율 분포 percentile 지표로 대체 — 유사 공고의 실제 낙찰가율 분포에서
      추천가율의 위치를 연속값으로 산출하고, 라벨을 "시장 경쟁력" 대신 "가격 위치(유사 공고 분포
      대비 추정)"류의 정직한 표현 + 표본 수 병기로 교체한다. peer 그룹은 업무구분/세그먼트에 더해
      9번 가격 레짐 라벨을 조건화 키로 사용한다(레짐 간 낙찰률 분포 형태가 근본적으로 달라 레짐
      혼합 분포의 percentile은 무의미). 분자/분모 base 정합(사업금액 기준 통일)을 명시한다.
      보조안: predictor price_range/candidate 밴드 내 위치 기반 연속 점수. 구현은 순수 함수
      resolver(분포 입력 주입) + 선언 테이블로 두고, 표본 부족 시 fallback은 현행 4버킷 유지가 아니라
      `unknown` 표기를 우선한다(저표본에서 계단값 % 노출 문제 (a) 재발 방지).
    - 전제/게이트: ① 사용자 노출값 + persist 필드라 값 정의 변경은 G-3 관찰 종료 후(지표 연속성),
      ② probability blend 0.18 항 입력이 바뀌므로 특성화·백테스트 재검증 필수(위 12번 split 정책 준수),
      ③ 분포 계산 time cutoff 필수 — 라이브는 해당 공고 개찰/공고일 이전 정산분만, 백테스트 replay는
      as-of 재구성으로 미래 정산 누수 차단, ④ 최소 표본 임계는 11번의 최소 표본 수 + prior shrinkage
      기계를 재사용, ⑤ 기존 persist 값과의 비교 가능성 확보(신 필드 병행 또는 마이그레이션 계획),
      ⑥ ml-reviewer 검수 + 정직 명세 라벨 리뷰, ⑦ 프론트 라벨/툴팁 변경은 ko 문구 리뷰 포함하되
      13번의 보고서/UX 노출 계획(표본 수·분모·레짐 병기)과 같은 표면에서 한 번에 설계(라벨 재설계
      중복 방지).
    - 산출물: competitiveness resolver(순수 함수 + 선언 테이블) · 특성화/차등 테스트 ·
      BidSummary 라벨 갱신(13번 UX 표면 참조) · 개선 전후 백테스트 비교 리포트(고정 JSON 경로).

## 원격 데이터/모델 접근 로드맵

2026-07-02 기준 로컬 사용량과 Supabase Pro 기본 제공량을 대조한 결과, 현재 모델 아티팩트와
학습 데이터 산출물을 Supabase Storage로 옮길 여유는 충분하다.

- 현재 `models/` 사용량: 약 471MB. Supabase Pro Storage 기본 제공량 100GB 대비 0.5% 미만이다.
- 현재 로컬 Postgres DB 크기: 약 711MB. Supabase Pro DB disk 기본 제공량 8GB 대비 약 9% 수준이다.
- Storage 여유는 충분하지만, KONEPS 원천/개찰 데이터는 모델 아티팩트보다 빠르게 증가할 수 있으므로
  DB disk 사용량은 별도 모니터링 대상으로 둔다.
- Supabase DB backup/PITR은 데이터베이스 대상이며 Storage object를 같은 방식으로 복구해주지 않는다.
  모델 파일, 학습 snapshot, release manifest는 checksum과 별도 export/mirror 정책이 필요하다.

전환 방향:

1. Supabase 프로젝트 준비: Postgres `vector` extension을 활성화하고 Alembic migration을 staging Supabase에
   적용한다. 운영 API, 학습 worker, read-only 분석 계정의 권한을 분리한다.
2. DB migration rehearsal: 로컬 Postgres에서 dump/restore를 수행한 뒤 row count, schema drift,
   주요 FK 정합, 최신 낙찰 holdout 백테스트 결과가 유지되는지 비교한다.
3. 모델 아티팩트 Storage 이전: `models/manifests`, `models/predictors`, `models/training-runs`를 private bucket으로
   옮기고 active release manifest에 checksum, artifact URI, 학습 데이터 snapshot id를 기록한다.
4. S3 호환 endpoint 지원: 현재 `ML_RELEASE_OBJECT_STORAGE_URL=s3://...` 경로는 boto3 S3 클라이언트를 사용하므로,
   Supabase Storage S3 endpoint를 명시할 수 있는 `ML_RELEASE_OBJECT_STORAGE_ENDPOINT_URL` 계열 설정과
   write/read/delete preflight를 추가한다.
5. 런타임 설정 분리: app/worker/training-worker가 `DATABASE_URL`, pooler mode, active manifest URI,
   local cache dir를 환경별로 다르게 받을 수 있게 한다. migration, dump/restore, 장기 job은 direct/session
   연결을 우선하고, 일반 API traffic은 pooler 사용을 검토한다.
6. 백업/복구 정책: DB 백업과 Storage 백업을 별도 runbook으로 관리한다. Storage는 release manifest,
   checksum 목록, 주기적 mirror export를 기준으로 복구 가능성을 검증한다.
7. 보안 경계: 클라이언트가 원천 학습 데이터나 모델 아티팩트에 직접 접근하지 않게 하고, backend/service
   credential만 private bucket과 학습 테이블에 접근한다. RLS는 client direct access가 필요한 테이블부터 적용한다.
8. 검증 gate: `alembic upgrade head`, schema drift check, 최신 낙찰 holdout, 업무구분별 wide holdout,
   해양/엔지니어링 holdout, smoke test를 Supabase staging 연결로 통과해야 운영 전환 후보로 본다.

### 운영 자동화

- scheduled smoke
- synthetic experiment preset
- operations dashboard
- ML release preflight
- worker/beat 재시작 검증

### 제품/사업

- 사용자 웹과 관리자 웹 분리
- 알림 피로도 관리
- 투찰 보고서 메일 전달과 알림 채널별 delivery evidence
- 투찰 선택 UX
- 리포트와 과금 근거
- 개인정보/사업자정보 보호

## 다음 우선순위

1. G-2 운영 증적 축적: 3개 이상 가상 사업자별 profile, strategy, notification channel, strategy monitor, decision experiment, synthetic experiment, G-2 evidence ledger를 `reports/g2-evidence/`와 `collect_g2_evidence` snapshot으로 N일 단위 저장한다.
2. G-2 blocking gap 해소: `/api/v1/analytics/g2-evidence`의 `blocking_gaps`를 operator별 TODO로 관리하고 `open`/`triaged`/`accepted_hold`를 모두 unresolved로 다룬다. `mixed_scope`/`missing` 상태를 제거하고, slug-only synthetic result는 operator_id-scoped evidence로 재실행 또는 보정한다.
3. G-1 표본 실행: sample-gap candidates를 dry-run으로 검토하고 승인 후 synthetic evidence run을 enqueue하여 operator_id-scoped settled sample 증적을 쌓는다.
4. G-2 알림 대상 검증: 사업자별 Telegram/app notification 대상 식별자, `dry_run_only`, masking, 실제 송신 가능 범위를 운영표로 관리한다.
5. G-0 관찰: scheduled smoke 핵심 phase green을 7일 이상 확보하고 실패 원인을 dashboard와 문서만으로 구분한다.
6. G-2 exit review: `docs/operations/g2-exit-review-template.md`의 manifest/checklist로 3개 이상 operator가 exit gate를 만족하는지 판정한다.
7. 추천 품질 세그먼트 후속: `procurement_rate_band`보다 세밀한 `price_regime_features`를 만들고,
   `floor_bound`/`near_100`/`deep_discount`/`ambiguous` 레짐별 calibration, recommended selector 분리,
   legal floor/예정가격 분모 품질 검사, 기관 group holdout과 최신 N건 rolling holdout을 구현한다.
8. API/OpenAPI 타입 정합: API schema 변경 시 `npm --prefix frontend run sync-types`와 `check:sync-types`를 실행해 generated frontend type drift를 막는다.
9. G-3 전까지 SaaS 멀티테넌트 전체 전환은 보류한다.

## 관련 문서

- `README.md`: 현재 시스템 개요와 실행 방법
- `CLAUDE.md`: 에이전트 작업 지침
- `docs/operations/g2-exit-agent-plan.md`: G-2 exit 기반 병렬 작업 완료 기록과 잔여 TODO
- `docs/operations/g2-evidence-runbook.md`: 3개 이상 가상 사업자의 G-2 evidence를 N일 단위로 반복 실행하고 exit review를 남기는 운영 절차
- `docs/operations/g2-exit-review-template.md`: G-2 exit review 문서 양식, evidence manifest 구조, approve/hold 판정 기준
- `docs/operations/roadmap-next-agent-plan.md`: 최근 완료된 병렬 작업 기록과 후속 gap
- `docs/operations/latest-award-holdout-backtest.md`: 최신 낙찰결과 holdout 백테스트 절차와 개선 전후 수치
- `docs/operations/procurement-segment-improvement-notes.md`: 조달 세그먼트별 투찰가 예측 개선 축과 후속 과제
- `docs/operations/ml-release-business-group.md`: business group별 ML release guardrail과 holdout 검증 절차
- `docs/operations/development-notebook-tasks.md`: 개발 노트북에서 진행 가능한 남은 작업 목록
- `docs/operations/test-operating-server-tasks.md`: 테스트/운영 서버와 실제 데이터가 필요한 남은 작업 목록
- `docs/marine-engineering-gate.md`: 엔지니어링협회 가입 회사 대상 해양/항만 기술용역 게이트와 면허/키워드 기준
- `docs/superpowers/specs/2026-07-03-business-number-guided-onboarding-design.md`: 사업자번호 기반 반자동 온보딩 설계
- `docs/production-smoke-test.md`: 운영 smoke test 절차
- `docs/api/index.md`: HTTP API 레퍼런스
