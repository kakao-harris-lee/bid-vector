# Optimal Bid Analysis Roadmap

## 목표

이 서비스의 목표는 **한 운영자가 원하는 공고를 빠르게 선별하고, 최적 투찰가와 실행 타이밍을 분석해 최종 낙찰 확률을 높이는 것**이다.

핵심 원칙은 다음과 같다.

1. 단순 공고 알림이 아니라 **낙찰-oriented decision support**를 제공한다.
2. 웹은 전략/설정/이력 관리 중심으로, 텔레그램은 즉시 알림/빠른 액션 중심으로 설계한다.
3. 분석 결과는 항상 `투찰`, `검토`, `보류` 중 하나의 실행 가이드로 귀결되어야 한다.
4. 실제 투찰/낙찰 결과를 다시 수집해 다음 추천 품질을 개선한다.

## 목표 워크플로우

### 1. 전략 입력

- 회사 프로필 관리
- 관심 업종/지역/예산/면허/키워드 설정
- 발주처 선호/제외 규칙 설정
- 알림 임계치 설정

### 2. 후보 공고 감시

- KONEPS 공고 수집
- 전략 조건 기반 필터링
- 신규 후보군 생성
- 중요 공고 즉시 알림

### 3. 다각도 분석

- 자격 적합성 분석
- 의미 기반 유사 공고 비교
- 가격/시장/경쟁도 분석
- 낙찰 가능성 및 실행 우선순위 계산

### 4. 실행 가이드

- `bid_now`, `review`, `skip` 중 하나의 액션 추천
- 추천 투찰가 및 근거 제공
- 텔레그램/웹에서 즉시 상태 변경

### 5. 결과 피드백

- 실제 투찰 여부 저장
- 낙찰/유찰 결과 저장
- 이후 추천 정확도 개선

## 진행 상태 업데이트

기준일: 2026-05-18

아래 체크는 현재 코드베이스에서 확인 가능한 백엔드/API/테스트 구현 기준이다. 이 저장소에는 별도 프론트엔드 화면 컴포넌트가 없어, 화면 자체가 아니라 웹 클라이언트가 바로 소비할 수 있는 API 연결 상태를 기준으로 판단한다.

### 확인된 완료 구현

- 통합 공고 분석 API: `POST /api/v1/operations/opportunity-analysis`
- 입찰 판단 저장/상세/타임라인 API: `POST/GET /api/v1/operations/bid-decisions`, `GET /api/v1/operations/bid-decisions/{id}`, `GET /api/v1/operations/projects/{project_id}/bid-decision-timeline`
- 웹 대시보드 집계 API: `GET /api/v1/operator/dashboard`
- 운영자 프로필/전략 API: `GET/PUT /api/v1/operator/profile`, `GET/PUT /api/v1/operator/strategy`
- 전략 후보 미리보기/동기 실행/비동기 실행/실행 이력 API
- KONEPS 공고/개찰/낙찰 결과 수집 및 `Project`, `HistoricalData`, `TenderResult` 연결
- 전략 기반 주기 모니터링 작업과 Celery beat 스케줄 구성
- 고우선순위 입찰 판단만 텔레그램으로 전달하고, 인라인 버튼으로 `투찰`, `검토`, `보류` 상태 변경
- 텔레그램 `/strategy`, `/strategy_set`, `/strategy_clear` 명령과 `/strategy` 버튼 기반 단계형 편집으로 감시 전략 조회/수정/초기화
- 발주처/업종별 히스토리, 예정가격 구간, 선택 번호 패턴, 피드백 보정을 반영한 가격 예측
- 보수/기준/공격 3개 투찰률 시나리오 제공
- 실제 투찰과 낙찰 결과를 비교하는 피드백/정확도 분석 API

### 남은 핵심 갭 및 운영 확인

- 실제 프론트엔드 앱이 별도 저장소에 있다면 `GET /api/v1/operator/dashboard` 응답을 화면 컴포넌트에 연결해야 한다.
- 실제 KONEPS/Telegram 외부 환경에서 한 주기 smoke test가 필요하다. 현재 저장소 기준 회귀 테스트는 추가됐다.

### 검증 결과

- `python3 -m py_compile app/services/notifications/telegram_strategy.py app/services/notifications/update_processor.py app/api/operator.py app/api/operations.py app/api/analytics.py app/schemas/schemas.py`: 통과
- `pytest -q tests/test_operator.py tests/test_operations.py`: `76 passed`
- `pytest -q`: `164 passed, 1 skipped`

## 단계별 진행 체크

### Phase 1 — 통합 분석 API 정착

- [x] 공고 하나에 대해 적합성/유사도/가격/행동 추천을 묶은 통합 분석 API 제공
- [x] 분석 결과를 웹 대시보드 카드/상세 화면에 연결
  - `GET /api/v1/operator/dashboard`가 분석 진입점, 최근 입찰 판단, 모니터링 실행 이력, 피드백 요약을 카드형 응답으로 제공한다.
- [x] 텔레그램 고우선순위 분석 메시지 포맷 최적화

### Phase 2 — 전략 입력 모델 확장

- [x] 운영자 입찰 전략(Profile + Watch Rules) 스키마 추가
- [x] 웹에서 전략 조건 수정 API 추가
- [x] 텔레그램 간단 등록/수정 플로우 추가
  - `/strategy`로 조회하고 버튼 기반 단계형 편집 또는 `/strategy_set`, `/strategy_clear`로 관심 업종/지역/키워드/예산/임계치/알림 범위/후보 수를 수정한다.

### Phase 3 — 모니터링 자동화

- [x] 전략 조건 기반 주기 모니터링 작업 추가
- [x] 새 공고 후보 자동 생성
- [x] 분석 후 high-priority 후보만 알림 발송

### Phase 4 — 낙찰 중심 가격 분석 고도화

- [x] 발주처/업종 기준 historical data 사용
- [x] 예정가격/사정률 패턴 반영
- [x] 추천 금액 1개가 아니라 후보 구간 3개 제공
- [x] 보수/기준/공격 시나리오 지원

### Phase 5 — 학습 루프 완성

- [x] 실제 투찰/낙찰 결과 기록 자동화
- [x] 분석값 대비 실제 결과 비교 리포트
- [x] 추천 정확도 개선용 피드백 데이터셋 축적

## 다음 우선 운영 순서

1. **별도 프론트엔드 클라이언트 연결 확인**
   - `GET /api/v1/operator/dashboard`의 `cards`, `recent_decisions`, `recent_monitor_runs`, `feedback_summary`, `action_hrefs`를 실제 화면에 매핑한다.
2. **운영 smoke test**
   - 실제 KONEPS 수집, 전략 모니터링, 텔레그램 알림, 피드백 보정이 한 주기에서 끊기지 않는지 운영 로그로 검증한다.
3. **운영 배포 preflight**
   - 실제 object storage credential/IAM 환경에서 `preflight-rollout`을 실행하고 manifest, signature, artifact checksum, write/delete probe 결과를 배포 체크리스트에 남긴다.

## 성공 기준

- 운영자가 공고 1건을 열었을 때, 별도 계산 없이 바로 행동할 수 있어야 한다.
- 분석 결과는 항상 다음을 포함해야 한다.
  - 적합성
  - 낙찰 가능성
  - 추천 투찰가
  - 주요 리스크
  - 권장 액션
- 알림은 많아도 좋지 않다. **실행 가치가 높은 공고만 빠르게 전달**되어야 한다.
