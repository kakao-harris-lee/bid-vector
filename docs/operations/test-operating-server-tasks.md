# Test and Operating Server Tasks

기준일: 2026-07-03

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
  queue wait p95/p99, API/worker RSS 기준선을 같은 git SHA에서 수집한다.
- idle 측정과 승인된 synthetic operator preview 부하 측정을 분리하고, 운영 사용자나 실제
  알림 대상에는 부하 작업을 실행하지 않는다.
- GET similarity가 API에서 모델을 로드하지 않는지, preview 부하 중 ops 큐가 inference
  runtime만큼 지연되지 않는지를 개선 전후 동일 명령으로 검증한다.
- 서버 판정 전까지 로컬 수치를 운영 SLO 달성 근거로 사용하지 않는다.

## 원격 데이터/모델 전환

- Supabase staging migration rehearsal: dump/restore, row count, schema drift, FK 정합, holdout, smoke test를 staging DB에서 검증한다.
- 모델 아티팩트 Storage 이전: private bucket, checksum, release manifest, mirror export, restore 가능성을 확인한다.
- runtime 설정 분리: API/worker/training-worker의 DB 연결, pooler mode, active manifest URI, local cache dir를 환경별로 검증한다.
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
