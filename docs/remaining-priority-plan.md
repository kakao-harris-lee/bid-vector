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
- release manifest predictor promotion gate, predictor 성능 추세 API, rollout preflight
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

- `pytest -q` 기준 전체 `151 passed, 1 skipped`
- `docker compose config --quiet` 및 `docker compose --profile tasks config --quiet` 통과

## 남은 핵심 과제 요약

### 1. 예측 엔진 운영화

실제 advanced predictor 추론, rolling backtest `auto` 선택, manifest promotion gate, 기간 버킷 성능 추세, training 산출물 검증 리포트, gate policy preset, rollout preflight가 들어갔다. 배포 절차에서는 실제 운영 credential/IAM 값으로 preflight를 실행하면 된다.

- 운영 배포 환경에서 `preflight-rollout` 실행

### 2. 실험 운영 자동화

추천 실험 계획과 persisted experiment run은 이미 있고, 수동 제어 및 threshold / workload / category feedback loop, dashboard action payload, 이력 기반 recommendation ranking, category/threshold 세부 `parameter_recommendation`까지 연결됐다.

- 장기 적용 이력 기반 추천 변화 폭 / confidence / 추천 사유 payload 반영 완료

### 3. 실행 인프라 안정화

- production-grade broker / result backend 운영 관측성은 operations dashboard에 연결됨
- release manifest object storage rollout preflight는 운영 credential/IAM 환경에서 실행 필요

### 4. 실시간 웹 이벤트

- 이벤트 인증/권한 검사
- 다중 프로세스/다중 worker 배포 시 pub/sub fanout
- 필요 시 event replay 범위와 retention 정책 정리

### 5. 운영 보고용 집계 보강

- worker queue 지연 / Telegram 전송률 / 모델 릴리즈 상태 카드는 구현됨

## 우선순위 선정 기준

우선순위는 아래 기준으로 정렬한다.

1. 낙찰 확률 개선에 직접 기여하는가
2. 이미 구현된 코드 위에 자연스럽게 얹을 수 있는가
3. 잘못 설계하면 재작업 비용이 큰가
4. 운영 안정성과 데이터 신뢰도를 같이 올리는가
5. 백엔드 저장소 단독으로 완결 가능한가

## 권장 우선순위

### 완료 — production task/broker 운영 관측성

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

#### 구현된 결과

- `GET /api/v1/analytics/operations-dashboard`가 `tasks` summary를 함께 반환한다.
- Celery broker/result backend URL은 credential을 마스킹해서 노출한다.
- queue route, worker runtime 설정, stale/failed/retry task 집계와 `task_broker_health`, `task_stale_queue`, `task_failure_rate` 카드가 추가됐다.

### 완료 — realtime 운영화

WebSocket realtime stream은 단일 프로세스 개발 환경에서는 동작하지만, 운영 환경에서는 인증과 다중 프로세스 fanout 경로가 아직 약하다.

#### 1순위 작업 범위

- WebSocket 인증
- 다중 프로세스 pub/sub fanout
- 이벤트 재연결/누락 보정 정책 정리

#### 1순위 완료 기준

- dashboard client가 인증된 realtime stream만 구독할 수 있어야 한다.
- 여러 API worker가 떠 있어도 동일한 이벤트가 안정적으로 전달되어야 한다.

#### 구현된 결과

- `WS /api/v1/realtime/events`는 기본적으로 operator access token을 요구한다.
- `REALTIME_FANOUT_BACKEND=postgres` 설정 시 PostgreSQL `LISTEN/NOTIFY` 기반 fanout listener가 앱 lifecycle에서 시작/종료된다.
- manager는 local broadcast와 cross-process fanout 수신을 분리하고, 자기 프로세스가 발행한 fanout echo는 무시한다.

### 완료 — 실험 운영 고도화

실험 계획과 실행 이력은 저장되고, 수동 제어와 threshold/workload/category 적용까지 가능하다. 이제 운영 화면에서 바로 쓰기 좋은 payload와 이력 집계를 다듬는 단계다.

#### 1순위 작업 범위

- 성공 / 실패 / 보류 실험 이력 기반 정렬 개선
- 적용 이력/중복 적용 방지 집계 보강
- 실험 이력 필터/정렬 payload 구조 정리

#### 1순위 우선 검토 파일

- `app/services/decision_analytics.py`
- `app/services/decision_experiments.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`

#### 1순위 완료 기준

- 운영자가 적용 가능한 실험과 이미 적용된 실험을 쉽게 구분할 수 있어야 한다.
- 실험 적용 결과가 다음 추천 로직과 운영 설정 변경에 감사 가능한 형태로 남아야 한다.

#### 구현된 결과

- `GET /api/v1/analytics/decision-experiments`가 `sort`, `outcome`, `application_status` 필터를 지원한다.
- 각 run은 `review_bucket`, `review_priority`, `review_reason`을 포함한다.
- list response는 success/pending/rollback/application/review bucket count를 함께 반환한다.

### 완료 — 추가 운영 카드 확장

prediction observability, operations dashboard, task/broker, realtime 운영화가 정리됐다. 다음은 외부 채널과 모델 릴리즈 상태를 같은 dashboard 카드 체계에 묶는 단계다.

#### 1순위 작업 범위

- Telegram 전송률 / 실패 원인 집계
- 모델 릴리즈 상태 카드
- training/backtest 결과 카드

#### 1순위 완료 기준

- 운영자가 알림 채널과 모델 릴리즈 상태를 operations dashboard에서 같이 확인할 수 있어야 한다.

#### 구현된 결과

- Telegram delivery 결과가 `telegram.delivery` analytics event로 저장된다.
- operations dashboard가 notification/Telegram 전송률, 실패 원인, 최근 실패를 반환한다.
- operations dashboard가 ML release manifest, signature 상태, predictor promotion gate, backtest sample/error summary를 반환한다.

### 완료 — 모델 학습/검증 고도화

모델 릴리즈/게이트/관측성은 운영 카드까지 연결됐다. 다음은 학습 데이터 품질과 artifact 비교 리포트를 강화하는 단계다.

#### 1순위 작업 범위

- 학습 dataset 품질 검증
- 모델 artifact 비교 리포트
- training 결과와 release gate 입력 간 연결 강화

#### 1순위 완료 기준

- 학습 산출물이 release manifest와 gate에서 바로 검증 가능한 리포트를 남겨야 한다.

#### 구현된 결과

- price predictor training run이 `dataset-quality.json`을 생성해 sample depth, project/agency diversity, linked result coverage, reserve pattern coverage, bid-rate variance를 검증한다.
- training run이 `artifact-comparison.json`을 생성해 historical / LSTM / ensemble artifact를 rolling holdout 기준으로 비교한다.
- manifest 생성 시 artifact comparison report가 predictor promotion gate 입력으로 연결된다.

### 완료 — 실험 추천 운영 루프 고도화

실험 run의 적용/보류/실패 이력은 dashboard payload로 정리됐다. 다음은 장기 적용 결과를 추천 로직에 다시 반영하는 단계다.

#### 1순위 작업 범위

- 장기 적용 결과를 experiment recommendation score에 반영
- 반복 실패/보류 segment의 추천 감점
- 성공 적용 segment의 후속 실험 제안 강화

#### 1순위 완료 기준

- 실험 추천이 최근 지표뿐 아니라 과거 적용 결과의 신뢰도를 함께 반영해야 한다.

#### 구현된 결과

- `GET /api/v1/analytics/decision-recommendations`가 최근 experiment run 이력을 함께 요약해 `experiment_history`를 반환한다.
- 각 recommendation이 `priority_score`, `history_adjustment`, supporting metric 내 `experiment_history`를 포함한다.
- 성공 후 적용된 실험 계열은 후속 추천 우선순위가 올라가고, 반복 실패/롤백/보류 계열은 우선순위와 urgency가 낮아진다.

### 완료 — promotion gate 운영 보정

모델 학습 산출물 리포트와 manifest gate 연결은 들어갔다. 다음은 운영 데이터에 맞는 gate 기준과 릴리즈 정책을 더 구체화하는 단계다.

#### 1순위 작업 범위

- promotion gate threshold를 운영 데이터 분포와 release tier에 맞춰 보정
- artifact comparison report의 dataset quality 상태를 gate reason에 더 직접 반영
- 릴리즈 정책별 require_report / min sample / max error 기준 preset 정리

#### 1순위 완료 기준

- manifest gate 판단이 단일 고정 threshold가 아니라 운영 정책에 맞는 기준을 명확히 노출해야 한다.

#### 구현된 결과

- predictor promotion gate가 `standard`, `canary`, `strict`, `advisory` rollout policy preset을 지원한다.
- gate threshold payload가 active policy, policy label, configured threshold, dataset quality floor를 함께 노출한다.
- artifact comparison report의 `dataset_quality_status`가 gate metrics와 failure reason에 반영된다.
- operations dashboard의 ML release summary가 latest gate policy와 dataset quality status를 반환한다.

### 완료 — 운영 credential/IAM rollout 검증

release manifest object storage publish/apply 경로는 구현됐다. 다음은 실제 운영 credential/IAM 기준에서 실패 원인을 명확히 드러내는 rollout 검증 경로를 정리하는 단계다.

#### 1순위 작업 범위

- object storage credential/IAM preflight 검증
- manifest signature required 모드 운영 체크
- publish/apply 실패 원인 payload 정리

#### 1순위 완료 기준

- 운영자가 rollout 전에 credential, bucket/prefix, signature requirement 문제를 사전에 확인할 수 있어야 한다.

#### 구현된 결과

- `preflight-rollout` CLI와 `make ml-release-preflight`가 manifest 로드, signature required 모드, artifact 경로, object storage write/delete probe를 검증한다.
- `file://` target과 `s3://bucket/prefix` target에 대해 bucket/prefix, credential/IAM, write/delete 실패를 check별 `status`, `detail`, `failure_reasons`로 반환한다.
- `publish_release_manifest`가 원격 publish 전에 동일 preflight를 실행해 실패 원인을 구조화된 payload로 드러낸다.

### 완료 — 실험 세부 추천값 산식 보강

장기 experiment run 이력은 recommendation ranking에 반영됐다. 다음은 성공/실패/보류 적용 결과를 category/threshold 세부 파라미터 추천값 자체에 더 직접 반영하는 단계다.

#### 1순위 작업 범위

- 성공 적용 segment의 category focus / threshold delta 추천값 보정
- 반복 실패/보류 segment의 추천 변화 폭 제한
- 적용 이력별 confidence와 추천 사유 payload 정리

#### 1순위 우선 검토 파일

- `app/services/decision_analytics.py`
- `app/services/decision_experiments.py`
- `app/schemas/schemas.py`
- `tests/test_decision_analytics.py`

#### 1순위 완료 기준

- recommendation ranking뿐 아니라 실제 추천 파라미터가 장기 적용 결과를 반영해야 한다.

#### 구현된 결과

- `GET /api/v1/analytics/decision-recommendations`가 threshold/category 추천에 `parameter_recommendation`을 포함한다.
- 성공 적용 이력은 추천 delta를 키우고, 반복 실패/롤백/보류 이력은 추천 변화 폭과 confidence를 낮춘다.
- experiment plan의 `suggested_change`와 `parameter_recommendation`이 같은 delta를 공유한다.
- threshold/category 실험 적용 API가 과거 동일 recommendation 이력을 조회해 실제 apply delta를 동적으로 조정한다.

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

### 완료 — WebSocket 실시간 이벤트 운영화

WebSocket 기본 레이어와 주요 이벤트 브로드캐스트가 있고, 운영 배포를 위한 인증과 PostgreSQL fanout 경로도 추가됐다.

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

#### 구현된 결과

- 기본 WebSocket 인증은 operator access token 기반이다.
- `REALTIME_FANOUT_BACKEND=postgres` 설정으로 여러 API worker 간 이벤트 fanout이 가능하다.

### 완료 — 운영 보고용 집계 API 확장

decision analytics, prediction observability, operations dashboard 기반과 worker/외부 채널/릴리즈 상태 카드가 구현됐다.

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

#### 구현된 결과

- worker queue 지연 / 실패율 / 재시도 카드
- Telegram 전송률 / 실패 원인 카드
- 모델 릴리즈 / manifest / predictor backtest gate 카드

## 바로 다음 구현 묶음

다음 턴에는 아래 순서가 가장 효율적이다.

### 완료 — 모델 학습/검증 고도화

- 학습 dataset 품질 검증
- 모델 artifact 비교 리포트

### 완료 — 실험 운영 루프 고도화

- 장기 적용 결과를 다음 recommendation ranking에 반영
- 반복 적용/롤백 패턴 기반 실험 추천 억제

### 완료 — promotion gate 운영 보정

- 운영 데이터 분포 기반 gate threshold 보정
- release tier별 gate policy preset 정리

### 완료 — 묶음 A 운영 credential/IAM rollout 검증

- object storage 실제 credential 기준 publish/apply 경로 점검
- 운영 환경 manifest signature required 모드 검증

### 완료 — 묶음 B 실험 세부 추천값 산식 보강

- 장기 적용 결과를 category/threshold 추천값 산식에 직접 반영
- 성공/실패/보류 패턴별 추천 변화 폭과 confidence 보정

### 묶음 C — 운영 배포 preflight 실환경 실행

- 운영 object storage credential/IAM으로 `preflight-rollout` 실행
- bucket/prefix, signature required, write/delete probe 실패 원인을 배포 체크리스트에 반영

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
