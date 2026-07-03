# Development Notebook Tasks

기준일: 2026-07-03

이 문서는 `docs/roadmap.md`의 남은 작업 중 개발 노트북에서 진행 가능한 항목만 분리한다. 기준은 실제 운영 DB, KONEPS live response, Telegram/app 실송신, N일 운영 증적 없이도 로컬 코드, fixture, mock data, unit/integration test로 검증 가능한지 여부다.

## 진행 원칙

- 실제 데이터 품질이나 운영 성과를 판정하지 않는다.
- 기능은 schema, parser, selector, UI 상태, dry-run, verifier, report contract처럼 재현 가능한 단위로 만든다.
- 운영 서버가 필요한 작업은 fake success로 대체하지 않고 `test-operating-server-tasks.md`로 넘긴다.
- API schema가 바뀌면 OpenAPI type sync와 drift check를 함께 수행한다.

## 우선순위 1: 추천 품질/가격 레짐 기반

- `price_regime_features` schema 정의: `buyer_sector`, `buyer_type`, `notice_category`, `business_type_code`, `construction_or_service_type`, `contract_method`, `award_method`, `evaluation_method`, `price_submission_mode`, `denominator_type`, `legal_floor_bid_rate`, `reserve_price_context`, `amount_bucket`, `agency_recent_rate_profile`, `data_quality_flags`.
- 가격 레짐 라벨러: `floor_bound`, `near_100`, `deep_discount`, `ambiguous`와 confidence/reason을 반환한다.
- fixture 기반 extractor unit test: 공고 제목/본문/계약방식 문구, 2단계/규격가격분리, 협상/수의시담, 엔지니어링/해양, 물품 견적형, 분모 불일치 케이스를 고정한다.
- recommended selector 분리: 후보 생성(`conservative`/`base`/`aggressive`)과 최종 추천 선택을 분리하고 `recommended_selector_reason`을 남긴다.
- 레짐별 target contract: `floor_bound`는 하한 대비 bp, `near_100`은 100% 대비 할인폭, `deep_discount`는 세그먼트 분위수/rate bucket, `ambiguous`는 단일 추천보다 후보 범위와 검토 상태를 우선한다.
- 보고서/API 표시 schema: 투찰 보고서와 admin 화면에서 가격 레짐, confidence, 적용 하한, 분모, 기관 표본 수, 데이터 품질 flag, selector reason을 노출할 수 있게 한다.
- 과적합 방지 코드 경계: 기관명/업체명/공고명 raw memorization을 피하고, 기관별 최근 분포는 최소 표본 수/time cutoff/prior shrinkage 조건을 통과한 경우에만 feature로 사용한다.

## 우선순위 3: G-2 exit/OpenAPI 하드닝

- G-2 exit review builder/readiness checker 보강: manifest path, counted day, operator 수, unresolved gap, notification masking 검증을 더 명확히 실패 사유로 반환한다.
- gap register 개선: `open`, `triaged`, `accepted_hold`를 unresolved로 유지하고, `resolved`/`excluded`만 성공 근거로 허용한다.
- notification target verifier fixture 강화: nested metadata/target context에서 raw secret-like target, operator mismatch, non-canonical active Telegram, missing dry-run policy를 잡는다.
- OpenAPI drift guard: API schema 변경 후 `frontend/src/shared/types/openapi.d.ts`가 stale 상태로 남지 않게 `sync-types`/`check:sync-types` 경로를 유지한다.
- docs/API 계약 정합: generated API markdown은 직접 수정하지 않고 generation workflow로 갱신한다.

## 기타 개발 노트북 가능 작업

- 사용자 화면 mock/fixture UX: 공고 추천 사유, 가격 레짐 근거, 투찰 보고서, 메일 보내기 dry-run 상태를 표시한다.
- Admin mock/fixture UX: operator별 G-2 evidence, 예외 큐, bid lifecycle, settlement 상태를 읽기 전용으로 표시한다.
- 사업자번호 온보딩 UI/도메인 구조: 자동 확인값, 추론 후보, 사용자 확정값을 구분하는 모델과 화면을 구현한다.
- Email dry-run delivery path: `EmailNotificationService`, masked target, delivery log, 실패해도 보고서 생성 흐름을 막지 않는 best-effort 경로를 만든다.
- Supabase/S3 전환 코드: endpoint 설정, checksum, manifest, local cache, preflight test를 로컬/fixture로 검증한다.

## 완료 기준

- 새 로직은 fixture/unit test로 실패-성공 경로가 확인되어야 한다.
- API schema 변경 시 generated frontend type과 OpenAPI docs drift가 없어야 한다.
- 운영 데이터가 필요한 판정은 문서상 `requires test/operating server`로 남기고 로컬 성공으로 대체하지 않는다.
