# 기획 대비 잔여 과제 우선순위 및 실행 계획 (2026-05-11)

## 현재 기준선

현재 저장소는 초기 기획의 “백엔드 골격” 단계를 이미 넘어섰고, 다음 흐름이 실제 코드와 테스트로 검증된 상태다.

- 인증 / 운영자 프로필 / 전략 설정 API
- 프로젝트 / 입찰 CRUD
- 나라장터 수집 mock/live 경로와 `CrawlJob` / `HistoricalData` / `TenderResult` 적재
- 규칙 기반 + 의미 기반 분류, pgvector 기반 유사 공고 검색
- prediction dataset 추출, 통계 기반 historical predictor, feedback calibration, reserve pattern, guardrail 응답 필드
- predictor abstraction 및 `HistoricalStatisticalPredictor`, `LSTMBidRatePredictor`, `EnsembleBidRatePredictor` skeleton
- `BidDecisionRecord` 기반 입찰 추진 결정, score breakdown, margin / complexity / workload 반영
- decision detail / timeline / funnel / recommendation analytics
- recommendation → experiment plan 확장
- persisted `decision-experiments` 생성 / 목록 / 상세 / evaluate API
- Telegram 알림 / callback / polling / web notification fallback
- 전략 모니터링 preview / execute / history 및 in-process scheduler

현재 검증 상태:

- `pytest -q` 기준 전체 `113 passed`

## 남은 핵심 과제 요약

### 1. 예측 엔진 운영화

실제 advanced predictor 추론은 들어갔고, 이제 운영 비교/선택 체계를 마무리할 차례다.

- 모델 아티팩트 저장/로드 전략
- predictor selection 정책 및 백테스트 체계
- predictor 정확도 / fallback / guardrail 비교 집계

### 2. 실험 운영 자동화

추천 실험 계획과 persisted experiment run은 이미 있고, 수동 제어와 threshold feedback loop까지 연결됐다. 이제 반영 범위를 더 넓혀야 한다.

- workload / category 계열 실험 결과 반영
- 대시보드 action payload 정리
- 실험 적용 이력 관측성 강화

### 3. 실행 인프라 안정화

- `docker compose up -d` 실패 원인 해결
- production-grade broker / result backend 정리
- 백필 / 학습 / 재평가 작업을 API 요청 경로에서 분리

### 4. 실시간 웹 이벤트

- WebSocket 연결 관리 레이어
- 신규 후보 / 추천 / 실패 이벤트 브로드캐스트
- Telegram / WebSocket 공통 event schema 정리

### 5. 운영 보고용 집계 보강

- predictor별 정확도 비교 집계
- fallback / guardrail 적용 빈도 집계
- 크롤 성공률 / 전략 성과 / 최근 기간 카드형 집계 API

## 우선순위 선정 기준

우선순위는 아래 기준으로 정렬한다.

1. 낙찰 확률 개선에 직접 기여하는가
2. 이미 구현된 코드 위에 자연스럽게 얹을 수 있는가
3. 잘못 설계하면 재작업 비용이 큰가
4. 운영 안정성과 데이터 신뢰도를 같이 올리는가
5. 백엔드 저장소 단독으로 완결 가능한가

## 권장 우선순위

### 1순위 — 예측 엔진 운영화 (backtest / selection / observability)

현재 가장 가치가 큰 다음 확장은 실제로 동작하는 predictor 위에 비교·선택·관측성을 얹는 것이다.

#### 이유

- 기존 데이터셋 추출, guardrail, fallback 구조와 advanced predictor 추론이 이미 준비되어 있다.
- 가장 큰 제품 가치 상승 지점이기도 하다.
- 현재 analytics / decision engine과도 자연스럽게 연결된다.

#### 1순위 작업 범위

- predictor별 backtest 입력/출력 규약 정리
- predictor selection / fallback selector 고도화
- predictor 메타데이터 기반 정확도 비교 API 준비
- artifact 관리 전략 정리

#### 1순위 우선 검토 파일

- `app/services/prediction_dataset.py`
- `app/ai/price_prediction.py`
- `app/ai/predictors/base.py`
- `app/ai/predictors/historical.py`
- `app/ai/predictors/lstm.py`
- `app/ai/predictors/ensemble.py`
- `tests/test_prediction_predictors.py`
- `tests/test_predictions.py`

#### 1순위 완료 기준

- 데이터가 충분할 때 advanced predictor가 안전하게 선택/비교되어야 한다.
- 데이터/모델이 부족할 때 historical predictor로 안전하게 fallback 되어야 한다.
- 응답과 집계만 보고 어떤 predictor가 선택됐는지 추적 가능해야 한다.

### 2순위 — 실험 운영 고도화

실험 계획과 실행 이력은 저장되고, 수동 제어와 threshold 적용까지 가능하므로 이제 반영 범위를 넓히는 단계다.

#### 2순위 작업 범위

- workload / category 실험 결과를 운영 설정 반영 후보로 변환
- 성공 / 실패 / 보류 실험 이력 기반 정렬 개선
- 적용 이력/중복 적용 방지 집계 보강

#### 2순위 우선 검토 파일

- `app/services/decision_analytics.py`
- `app/services/decision_experiments.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`

#### 2순위 완료 기준

- 운영자가 실험을 단순 조회만이 아니라 실제로 종료/롤백하고 threshold에 반영할 수 있어야 한다.
- 실험 결과가 다음 추천 로직과 운영 설정 변경에 연결 가능한 구조가 되어야 한다.

### 3순위 — 실행 인프라 및 배치 경로 안정화

현재 스캐폴드와 로컬 친화성은 좋지만, 운영용 경로는 더 정리해야 한다.

#### 3순위 작업 범위

- `docker compose up -d` 실패 원인 복구
- Celery broker / result backend 전략 정리
- 수집 백필 / 데이터셋 리프레시 / 모델 평가 작업 분리
- 상태 조회 / 실패 로그 / 마지막 성공 시각 정리

#### 3순위 우선 검토 파일

- `docker-compose.yml`
- `Dockerfile`
- `app/tasks/celery_app.py`
- `app/tasks/jobs.py`
- `app/services/strategy_scheduler.py`
- `README.md`

#### 3순위 완료 기준

- 로컬 개발 환경에서 compose 기동이 재현 가능해야 한다.
- 무거운 작업이 API 요청 경로에서 분리되어야 한다.

### 4순위 — WebSocket 실시간 이벤트

Telegram은 이미 운영 가능한 수준이므로, 웹 대시보드 실시간 채널이 다음 단계다.

#### 4순위 작업 범위

- 연결 관리 레이어
- 신규 후보 / 추천 / 실패 / 전략 모니터링 이벤트 push
- notification payload와 실시간 event payload 정규화

#### 4순위 우선 검토 파일

- 신규 `app/services/realtime.py`
- 신규 `app/api/realtime.py` 또는 `app/api/routes.py` 확장
- `app/main.py`
- 신규 `tests/test_realtime.py`

#### 4순위 완료 기준

- polling 없이 핵심 이벤트를 푸시할 수 있어야 한다.
- 연결이 없더라도 기존 persisted notification 흐름은 유지되어야 한다.

### 5순위 — 운영 보고용 집계 API 보강

decision analytics는 이미 강하지만, prediction/infra 관측성은 더 필요하다.

#### 5순위 작업 범위

- predictor별 정확도 비교 API
- fallback / guardrail 적용 빈도 집계
- 크롤 성공률 / 전략 성과 / 기간별 카드형 집계

#### 5순위 우선 검토 파일

- `app/api/analytics.py`
- `app/api/operator.py`
- 신규 `app/services/analytics_reporting.py`
- 신규 `tests/test_analytics_reporting.py`

#### 5순위 완료 기준

- 운영 화면이 별도 후처리 없이 카드/차트 구성이 가능해야 한다.
- 모델 정확도와 전략 성과를 같은 축으로 비교할 수 있어야 한다.

## 바로 다음 구현 묶음

다음 턴에는 아래 순서가 가장 효율적이다.

### 묶음 A — predictor 비교/선택 체계

- predictor backtest 입력/출력 스키마 정리
- historical vs lstm vs ensemble 비교 기준 확정
- predictor별 테스트 fixture 설계

### 묶음 B — prediction observability

- fallback 빈도 / guardrail 빈도 / predictor 선택 비율 집계
- 정확도 비교 API 설계 및 구현

### 묶음 C — 실험 반영 범위 확장

- workload / category 계열 실험 결과 반영
- 적용 이력/중복 적용 방지 정리

## 범위 재정의 / 비우선 항목

- `다중 사용자 공정 분배 로직`
  - 현재 제품은 single-operator workflow 기준이다.
  - `Allocation`은 호환성 유지 수준으로 두고 핵심 확장은 `BidDecisionRecord` 중심으로 진행한다.
- `Redis 의존형 구조`
  - PostgreSQL 중심 구조를 유지한다.
  - Redis는 필수가 아니라 선택적 운영 구성으로만 검토한다.
- `프론트엔드 자체 구현`
  - 현재 저장소는 백엔드 중심이므로 UI 구현보다 API 계약과 실시간 payload를 우선한다.

## 성공 기준

이 계획이 잘 수행되면 다음 상태에 도달해야 한다.

- 수집 → 분석 → 추천 → 실험 → 결과 평가 흐름이 데이터 관점에서 끊기지 않는다.
- 예측기는 baseline과 advanced 구현이 공존하며 안전하게 fallback 된다.
- 운영자는 Telegram과 웹 양쪽에서 실행 가능한 수준의 신호를 받는다.
- 실험 결과가 실제 threshold 조정과 운영 개선으로 이어질 수 있다.
- 장기적으로 `LSTM/Ensemble` 실험과 운영을 반복할 수 있는 구조가 된다.
