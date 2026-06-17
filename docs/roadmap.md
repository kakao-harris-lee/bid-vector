# bid-vector 로드맵

기준일: 2026-06-17

이 문서는 `bid-vector`의 단계별 목표와 exit gate를 정리하는 단일 로드맵입니다. 오래된 계획 문서보다 현재 코드와 이 문서를 우선합니다.

## 현재 결론

0~2단계의 핵심 빌드는 대부분 완료되어 있습니다. 현재 병목은 기능 추가가 아니라 **운영 검증과 증적 축적**입니다.

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
| 1 | 가상 회사 실험실 | 구현됨, 데이터 축적 필요 | 업종/규모별 가상 회사에서 추천 품질이 검증되는가 |
| 2 | 독립 가상 사업자 운영 검증 | 다음 목표 | 각 회사가 독립 ID/사업자 정보로 서비스처럼 운영되는가 |
| 3 | 제한 실증 서비스 | G-2 후 착수 | 실제 사업자가 매일 써도 업무 시간이 줄고 추천이 유효한가 |
| 4 | SaaS/수수료 사업화 | G-3 후 착수 | 과금, 보안, 운영지원까지 견딜 수 있는가 |

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
- `frontend/src/features/synthetic-backtest/`

검증 축:

- 공사, 용역, 물품, 소프트웨어 등 입찰 종류별 후보 선별
- 소형/중형/대형 회사별 예산/면허/지역/시공능력 매칭
- historical backtest와 forward paper bidding 비교
- `win_rate_on_settled`, `bid_submission_rate`, 평균 오차율, 분야별 breakdown
- 가격 기준 추정 낙찰과 룰 기반 적격 게이트 추정의 차이

해야 할 일:

- synthetic company catalog를 실제 서비스 검증 목적에 맞게 정리
- 업종별 최소 정산 표본 수를 정하고 부족한 영역을 백필
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

- 백엔드는 여전히 단일 운영자 모델이 기본입니다.
- synthetic operator infrastructure는 있지만 SaaS 멀티테넌트는 아닙니다.
- 현재 프론트는 단일 SPA이며 사용자 웹/관리자 웹이 명확히 분리되어 있지 않습니다.

해야 할 일:

- 가상 회사별 로그인/프로필/전략/알림/결정 이력을 독립적으로 검증
- canonical operator fallback 제거 범위를 넓히고 데이터 격리 테스트 추가
- 사용자 웹과 관리자 웹의 정보 구조 분리
  - 사용자 웹: 회사 정보, 조건 설정, 추천 공고, 알림, 투찰 선택, 결과 확인
  - 관리자 웹: 백테스트, synthetic experiment, smoke test, 데이터 수집 상태, 정확도/통계, ML release 상태
- 가상 사업자별 알림 채널을 분리하거나 routing key를 명확히 한다.
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

1. G-0 관찰: scheduled smoke와 operations dashboard를 N일 모니터링한다.
2. G-1 표본 확보: synthetic operator별 settled sample과 category breakdown을 채운다.
3. G-1 리포트 고정: synthetic experiment preset과 정확도 리포트 형식을 고정한다.
4. G-2 설계 착수: 사용자 웹/관리자 웹 분리와 가상 사업자 독립 운영 범위를 작게 설계한다.
5. G-3 전까지 SaaS 멀티테넌트 전체 전환은 보류한다.

## 관련 문서

- `README.md`: 현재 시스템 개요와 실행 방법
- `CLAUDE.md`: 에이전트 작업 지침
- `docs/production-smoke-test.md`: 운영 smoke test 절차
- `docs/api/index.md`: HTTP API 레퍼런스
