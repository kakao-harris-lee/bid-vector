# 기획 대비 잔여 과제 우선순위 및 실행 계획 (2026-05-12)

## 현재 기준선

현재 저장소는 초기 기획의 “백엔드 골격” 단계를 이미 넘어섰고, 다음 흐름이 실제 코드와 테스트로 검증된 상태다.

- 인증 / 운영자 프로필 / 전략 설정 API
- 프로젝트 / 입찰 CRUD
- 나라장터 수집 mock/live 경로와 `CrawlJob` / `HistoricalData` / `TenderResult` 적재
- 규칙 기반 + 의미 기반 분류, pgvector 기반 유사 공고 검색
- prediction dataset 추출, 통계 기반 historical predictor, feedback calibration, reserve pattern, guardrail 응답 필드
- predictor abstraction 및 artifact-backed `HistoricalStatisticalPredictor`, `LSTMBidRatePredictor`, `EnsembleBidRatePredictor` 추론
- persisted prediction metadata 기반 predictor / fallback / guardrail / linked-result accuracy observability API
- rolling backtest 기반 `auto` predictor selection
- release manifest predictor promotion gate 및 predictor 성능 추세 API
- `BidDecisionRecord` 기반 입찰 추진 결정, score breakdown, margin / complexity / workload 반영
- decision detail / timeline / funnel / recommendation analytics
- recommendation → experiment plan 확장
- persisted `decision-experiments` 생성 / 목록 / 상세 / evaluate API
- successful threshold / workload / category experiment apply feedback loop
- experiment run별 application status / history / next action payload
- Telegram 알림 / callback / polling / web notification fallback / WebSocket realtime events
- 전략 모니터링 preview / execute / history 및 in-process scheduler
- 크롤 성공률 / 전략 성과 / 최근 실패 원인 기반 operations dashboard analytics

현재 검증 상태:

- `pytest -q` 기준 전체 `136 passed, 1 skipped`
- `docker compose config --quiet` 및 `docker compose --profile tasks config --quiet` 통과

## 남은 핵심 과제 요약

### 1. 예측 엔진 운영화

실제 advanced predictor 추론, rolling backtest `auto` 선택, manifest promotion gate, 기간 버킷 성능 추세는 들어갔다. 이제 모델 학습/검증 파이프라인과 gate 기준 운영 보정이 남았다.

- 모델 아티팩트 학습/검증 파이프라인 고도화
- promotion gate 기준을 실제 운영 데이터에 맞춰 보정
- predictor 성능 추세를 릴리즈 의사결정 카드로 확장

### 2. 실험 운영 자동화

추천 실험 계획과 persisted experiment run은 이미 있고, 수동 제어 및 threshold / workload / category feedback loop, dashboard action payload까지 연결됐다. 이제 성공/실패/보류 이력 기반 정렬과 장기 관측성 보강 단계다.

- 성공 / 실패 / 보류 실험 이력 기반 정렬 개선
- 적용 결과의 기간별 집계와 운영 카드 확장

### 3. 실행 인프라 안정화

- production-grade broker / result backend 운영 관측성 정리
- 실제 credential/IAM 기준 release manifest object storage rollout 검증

### 4. 실시간 웹 이벤트

- 이벤트 인증/권한 검사
- 다중 프로세스/다중 worker 배포 시 pub/sub fanout
- 필요 시 event replay 범위와 retention 정책 정리

### 5. 운영 보고용 집계 보강

- worker queue 지연 / Telegram 전송률 / 모델 릴리즈 상태 카드 확장

## 우선순위 선정 기준

우선순위는 아래 기준으로 정렬한다.

1. 낙찰 확률 개선에 직접 기여하는가
2. 이미 구현된 코드 위에 자연스럽게 얹을 수 있는가
3. 잘못 설계하면 재작업 비용이 큰가
4. 운영 안정성과 데이터 신뢰도를 같이 올리는가
5. 백엔드 저장소 단독으로 완결 가능한가

## 권장 우선순위

### 1순위 — production task/broker 운영 관측성

비동기 작업은 API 요청 경로에서 분리됐고 compose task profile도 준비됐다. 다음 큰 빈틈은 worker queue 지연, 실패율, 재시도, broker health를 운영 화면이 바로 볼 수 있게 만드는 것이다.

#### 이유

- task가 queued 상태로 남는 문제는 운영에서 바로 감지되어야 한다.
- broker/result backend 상태는 수집/알림/재평가 신뢰도에 직접 연결된다.
- 이미 operations dashboard 골격이 있어 카드 확장으로 자연스럽게 붙일 수 있다.

#### 1순위 작업 범위

- Celery queue/backend 설정 진단 payload
- 최근 task 실패/지연/재시도 집계
- broker health와 worker 분리 운영 상태 카드
- operations dashboard에 task health 카드 추가

#### 1순위 우선 검토 파일

- `app/tasks/celery_app.py`
- `app/tasks/jobs.py`
- `app/services/analytics_reporting.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`
- `tests/test_analytics_reporting.py`

#### 1순위 완료 기준

- 운영 화면이 worker/broker 상태를 카드로 표시할 수 있어야 한다.
- queued/stale/failed 작업 위험이 API 응답에서 명확히 드러나야 한다.

### 2순위 — 실험 운영 고도화

실험 계획과 실행 이력은 저장되고, 수동 제어와 threshold/workload/category 적용까지 가능하다. 이제 운영 화면에서 바로 쓰기 좋은 payload와 이력 집계를 다듬는 단계다.

#### 2순위 작업 범위

- 성공 / 실패 / 보류 실험 이력 기반 정렬 개선
- 적용 이력/중복 적용 방지 집계 보강
- 실험 이력 필터/정렬 payload 구조 정리

#### 2순위 우선 검토 파일

- `app/services/decision_analytics.py`
- `app/services/decision_experiments.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`

#### 2순위 완료 기준

- 운영자가 적용 가능한 실험과 이미 적용된 실험을 쉽게 구분할 수 있어야 한다.
- 실험 적용 결과가 다음 추천 로직과 운영 설정 변경에 감사 가능한 형태로 남아야 한다.

### 3순위 — 실행 인프라 및 배치 경로 안정화

현재 스캐폴드와 로컬 친화성은 좋지만, 운영용 경로는 더 정리해야 한다.

#### 3순위 작업 범위

- Celery broker / result backend 운영 정책 정리
- 수집 백필 / 데이터셋 리프레시 / 모델 평가 작업 관측성 보강
- 상태 조회 / 실패 로그 / 마지막 성공 시각 정리

#### 3순위 우선 검토 파일

- `docker-compose.yml`
- `Dockerfile`
- `app/tasks/celery_app.py`
- `app/tasks/jobs.py`
- `app/services/strategy_scheduler.py`
- `README.md`

#### 3순위 완료 기준

- 로컬 개발 환경에서 compose 설정이 재현 가능해야 한다.
- 무거운 작업은 API 요청 경로 밖에서 실행되고, 상태 조회가 가능해야 한다.

### 4순위 — WebSocket 실시간 이벤트 운영화

WebSocket 기본 레이어와 주요 이벤트 브로드캐스트는 구현됐다. 운영 배포에서는 인증과 다중 프로세스 fanout이 남는다.

#### 4순위 작업 범위

- WebSocket 인증/권한 검사
- Redis/RabbitMQ/PostgreSQL notify 등 다중 프로세스 pub/sub 선택
- event replay/retention 정책 정리

#### 4순위 우선 검토 파일

- `app/services/realtime.py`
- `app/api/realtime.py`
- `app/main.py`
- `tests/test_realtime.py`

#### 4순위 완료 기준

- 운영 배포에서 여러 API 프로세스 사이 이벤트가 누락되지 않아야 한다.
- 인증되지 않은 클라이언트가 이벤트 스트림을 열 수 없어야 한다.

### 5순위 — 운영 보고용 집계 API 확장

decision analytics, prediction observability, operations dashboard 기반이 생겼고, 남은 것은 worker/외부 채널/릴리즈 상태 카드 확장이다.

#### 5순위 작업 범위

- worker queue 지연 / 실패율 / 재시도 카드
- Telegram 전송률 / 실패 원인 카드
- 모델 릴리즈 / manifest 상태 카드

#### 5순위 우선 검토 파일

- `app/api/analytics.py`
- `app/services/analytics_reporting.py`
- `tests/test_analytics_reporting.py`

#### 5순위 완료 기준

- 운영 화면이 별도 후처리 없이 카드/차트 구성이 가능해야 한다.
- 모델 정확도와 전략 성과를 같은 축으로 비교할 수 있어야 한다.

## 바로 다음 구현 묶음

다음 턴에는 아래 순서가 가장 효율적이다.

### 묶음 A — task/broker 운영 관측성

- worker queue 지연 / 실패율 / 재시도 카드
- broker health 점검 payload

### 묶음 B — realtime 운영화

- WebSocket 인증
- 다중 프로세스 pub/sub fanout

### 묶음 C — realtime 운영화

- WebSocket 인증
- 다중 프로세스 pub/sub fanout

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
