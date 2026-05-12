# AGENT.md

## 목적

이 저장소는 `나라 장터 AI 입찰 서비스`를 위한 Python/FastAPI 백엔드입니다.
에이전트는 기존 코드를 유지·확장하는 방향으로 작업해야 하며, 이미 존재하는 API/AI 모듈을 버리고 새로 재구성하지 않습니다.

`first_plan.md`의 기획 내용을 기준으로 하되, 현재 저장소 상태를 우선 반영합니다.

## 현재 저장소 기준선

- 백엔드 프레임워크: FastAPI
- 데이터 계층: SQLAlchemy + PostgreSQL/pgvector 중심, 테스트는 SQLite 사용
- 비동기/스케줄링: in-process strategy scheduler + Celery + optional RabbitMQ/worker/beat profile 공존
- 운영 방향: Redis를 기본 전제로 두지 않고 PostgreSQL 중심 구조를 유지
- 현재 검증 상태: 로컬 `pytest -q` 기준 `125 passed, 1 skipped`, `docker compose up -d` + `/health` + `/api/v1/operator/strategy` smoke test 재검증 완료, `docker compose --profile tasks config` 해석 확인 완료
- Docker 이미지 프로필: `api-runtime`(기본), `api-embedding`, `api-training`, `api-ml-full`

### 현재까지 완료된 범위

- 인증 / 운영자 프로필 / 전략 설정 API
- 프로젝트 / 입찰 CRUD 및 운영자 overview / notification 조회
- 나라장터 수집 mock/live 경로, `CrawlJob` / `HistoricalData` / `TenderResult` 적재, `Project` 연결
- 규칙 기반 + 의미 기반 분류, pgvector 기반 유사 공고 검색
- 가격 예측 데이터셋 추출, 통계 기반 예측, feedback calibration, reserve pattern, guardrail 응답 필드
- predictor abstraction 및 `HistoricalStatisticalPredictor`, artifact-backed `LSTMBidRatePredictor` / `EnsembleBidRatePredictor` 추론
- opportunity analysis / bid recommendation / persisted bid decision engine
- `BidDecisionRecord` 기반 우선순위 / 추진 결정, score breakdown, margin / complexity / workload 반영
- 결정 상세 / 타임라인 / 퍼널 / 트렌드 / 세그먼트 / 기간 비교 / 추천 analytics
- 추천을 실험 계획으로 확장한 `decision-recommendations`
- 실험 실행 이력 / 평가 API (`decision-experiments`) 및 baseline vs current 비교
- 실험 run 수동 상태 변경 / 메모 갱신 / threshold 적용 feedback loop
- Telegram 알림 / callback / polling / 상태 동기화, 웹 알림 fallback
- 전략 모니터링 preview / execute / history 및 in-process scheduler
- `docker compose` 복구, healthcheck/env wiring 정리, CPU-only PyTorch + pip cache 기반 Docker build 최적화
- runtime / embedding / training / dev 의존성 분리 및 멀티타깃 Docker build 정리
- manifest 기반 ML artifact promotion 서비스/CLI 및 embedding rebuild 자동화
- manifest 추천값의 `.env` 반영 자동화 (`--write-env-file`)
- manifest apply 후 compose 재기동 + health 확인 + API 기반 embedding rebuild rollout 자동화
- Celery worker가 `app.tasks.jobs`를 명시적으로 import 하도록 정리해 out-of-process worker/beat 경로에서 task 등록 누락을 방지
- 외부 broker 사용 시 `CELERY_RESULT_BACKEND` 기본값을 PostgreSQL(`db+${DATABASE_URL}`)로 자동 승격하도록 정리
- optional `tasks` compose profile(`rabbitmq`, `worker`, `beat`) 및 관련 Makefile/문서 경로 추가

### 아직 핵심적으로 남은 범위

- `LSTM` / `Ensemble` 학습 파이프라인 및 모델 아티팩트 관리 고도화
- 예측기 성능 비교 / 백테스트 / predictor selection 자동화
- threshold 외 workload/category 계열 실험 결과 반영 범위 확장
- `docker compose up -d` 실패 원인 해결과 production-grade task/broker 정리
- WebSocket 기반 실시간 이벤트 전송
- 운영 대시보드용 모델 정확도 / 크롤 성공률 / fallback 빈도 집계 API 보강

## 작업 원칙

1. 기존 구조를 존중합니다.
   - API 라우트는 `app/api/`
   - AI/분석 로직은 `app/ai/`
   - 설정은 `app/core/`
   - DB 모델은 `app/models/`
   - Pydantic 스키마는 `app/schemas/`
   - 도메인 서비스는 `app/services/`

1. 한 번에 전부 갈아엎지 말고 작은 단위로 확장합니다.
   - 새 기능은 가능한 한 신규 모듈로 추가
   - 기존 public API 변경 시 반드시 테스트도 함께 수정

1. 기획 문서보다 현재 실행 가능한 코드베이스를 우선합니다.
   - `first_plan.md`는 목표 문서입니다.
   - 현재 구현과 충돌하면, 먼저 호환 가능한 중간 단계를 설계합니다.

1. 환경 변수는 코드에 하드코딩하지 않습니다. 신규 외부 연동(텔레그램, 크롤링 인증, DB 등)은 모두 `.env`에 추가합니다.

1. 테스트 가능성을 항상 유지합니다.
   - 비즈니스 로직은 라우트에서 직접 키우지 말고 함수/서비스로 분리
   - 테스트는 `tests/`에 추가

## 기능 상태 및 남은 작업

### A. 데이터 수집 및 적재 — 기반 완료, 운영 고도화 필요

이미 구현됨:

- `app/services/koneps/collector.py` 기반 mock/live 수집 경로
- `HistoricalData`, `TenderResult`, `CrawlJob`, `Project` 연결 적재
- 수집 API / 작업 상태 / 기본 회귀 테스트

남은 작업:

- live 수집 안정화(retry, backoff, selector drift 대응)
- 백필/주기 수집 작업 분리
- 수집 성공률 / 실패 원인 / 마지막 성공 시각 집계

### B. 맞춤형 공고 분류 및 유사도 — 기반 완료, 정밀도 보정 단계

이미 구현됨:

- 규칙 기반 적합도 계산
- 운영자 프로필 기반 필터링
- Sentence-Transformers + pgvector 유사 공고 검색

남은 작업:

- 면허/지역/업종 false positive 보정
- 분류 점수 calibration
- 설명 가능한 세부 reason 확장

### C. 사정률 예측 엔진 — 현재 최우선 확장 영역

이미 구현됨:

- 학습용 데이터셋 추출 서비스
- 통계 기반 historical predictor
- fallback / predictor 메타데이터 / guardrail 응답 구조
- artifact-backed `LSTM` / `Ensemble` predictor 추론

남은 작업:

- 모델 아티팩트 저장/로드 전략 정리
- predictor selection, 백테스트, 정확도 비교 API 추가

우선 검토 파일:

- `app/services/prediction_dataset.py`
- `app/ai/price_prediction.py`
- `app/ai/predictors/base.py`
- `app/ai/predictors/historical.py`
- `app/ai/predictors/lstm.py`
- `app/ai/predictors/ensemble.py`
- `tests/test_prediction_predictors.py`
- `tests/test_predictions.py`

### D. 입찰 추진 결정 / 실험 분석 — 핵심 완료, 운영 자동화 확장 필요

이미 구현됨:

- `BidDecisionRecord` 기반 추진 결정 엔진과 영속화
- 상세 / 타임라인 / 퍼널 / 추천 analytics
- recommendation → experiment plan 확장
- 실험 run 저장 / baseline snapshot / evaluate API
- 실험 수동 종료 / 롤백 / 메모 수정 API
- 성공 실험의 threshold 적용 feedback loop

남은 작업:

- workload / category 계열 실험 결과를 운영 설정으로 반영하는 범위 확장
- 운영 화면에서 바로 쓰기 좋은 action payload 정리

우선 검토 파일:

- `app/services/allocation.py`
- `app/services/decision_analytics.py`
- `app/services/decision_experiments.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`

### E. 알림 채널 — Telegram 완료, WebSocket 미완료

이미 구현됨:

- Telegram 메시지 포맷 / callback / polling / 상태 동기화
- 웹 알림 fallback 및 운영자 notification 조회

남은 작업:

- WebSocket 연결 관리 레이어
- 신규 후보 / 추천 / 실패 이벤트 브로드캐스트
- Telegram / WebSocket 공통 event schema 정리

### F. 비동기 / 실행 인프라 — compose 복구 완료, 운영 정리 필요

이미 구현됨:

- Celery 스캐폴드
- in-process scheduler 기반 전략 모니터링 실행
- task 상태 조회용 기본 경로
- `docker compose` 복구 및 로컬 smoke test/컨테이너 테스트 검증
- Docker build에서 CPU-only PyTorch wheel과 pip cache를 사용하도록 최적화
- 기본 API 이미지를 `api-runtime` 타깃으로 슬림화하고, `api-embedding` / `api-training` / `api-ml-full`로 분리
- optional RabbitMQ broker + Celery worker/beat compose profile 추가
- 외부 broker 사용 시 PostgreSQL result backend 자동 연동
- worker/beat가 현재 task 모듈을 명시 import하도록 정리

남은 작업:

- 백필 / 학습 / 재평가 작업을 API 요청 경로에서 완전히 분리
- training 컨테이너와 embedding 런타임 사이의 artifact promotion 자동화 스크립트/manifest 고도화
- 기본 CLI/manifest 경로는 구현됨 (`scripts/promote_ml_release.py`)
- 다음 단계는 release manifest 서명/보관 정책, `.env` 갱신 자동화, remote object storage 연계

### G. 분석 / 대시보드 집계 — decision analytics는 강함, prediction reporting 잔여

이미 구현됨:

- overview / summary / prediction feedback
- decision insights / funnel / recommendations / experiments

남은 작업:

- 모델별 정확도 비교 집계
- fallback 빈도 / guardrail 적용 빈도 집계
- 크롤 성공률 / 전략 성과 / 최근 기간 카드형 응답 정리

## 현재 권장 실행 순서

1. `C. 예측 성능 비교 / 백테스트 / selection 자동화`
2. `F. 백필 / 학습 / 재평가 작업의 API 경로 분리`
3. `E. WebSocket 실시간 이벤트`
4. `G. 운영 보고용 집계 API`
5. `D. workload/category 실험 반영 범위 확장`

`A/B`는 신규 구축 단계가 아니라 유지보수·정확도 보정 단계로 간주합니다.

## 설계 지침

### API 설계

- 라우터는 얇게 유지합니다.
- 핵심 로직은 `services` 또는 `ai` 모듈로 이동합니다.
- 외부 입력/응답은 반드시 `schemas`로 명시합니다.
- 관리자 기능은 일반 사용자 기능과 분리합니다.

### 데이터 모델 설계

기존 `User`, `Project`, `Bid`, `PricePrediction`, `Notification`, `Analytics`를 유지하면서,
아래 확장을 우선 검토합니다.

- 업체 프로필/자격 정보 (`CompanyProfile`)
- 과거 개찰 데이터 저장소 (`HistoricalData`, `TenderResult`)
- 발주처/프로젝트 메타데이터 및 임베딩 정보
- 입찰 추진 결정 기록 (`BidDecisionRecord`)
- 의사결정 실험 실행 이력 (`DecisionExperimentRun`)
- 크롤링 실행 이력 및 실패 로그 (`CrawlJob`)
- 운영자 전략 실행 이력 (`OperatorStrategyRun`)

### 비동기/배치 처리

무거운 작업은 요청-응답 사이클에서 직접 실행하지 않습니다.

- 크롤링
- 문서 대량 분석
- 모델 학습/재학습
- 대량 알림 발송

위 작업은 Celery 작업 큐로 분리하는 방향을 기본값으로 봅니다.

## 코드 스타일 및 품질 기준

- Python 3.11+ 기준, 현재 검증 환경은 Python 3.12
- 타입 힌트 우선
- 함수는 가능한 한 순수 함수로 설계
- 예외 메시지는 디버깅 가능한 수준으로 작성
- 로깅 추가 시 개인정보/민감 정보는 남기지 않음

테스트 기준:

- 신규 API: 정상/실패 케이스 최소 1개씩
- 신규 서비스 로직: 단위 테스트 우선
- 회귀 가능성이 높은 계산 로직: 고정 입력/출력 테스트 추가

## 보안 및 정책

- JWT/DB/Telegram 토큰은 `.env` 사용
- 법적 낙찰 하한선보다 낮은 추천값은 반환하지 않도록 검증 단계 추가
- 크롤링 대상 사이트 정책을 고려해 과도한 요청 금지
- 운영자 입찰 추진 결과는 감사 가능하게 저장

## 에이전트가 피해야 할 것

- 기존 FastAPI 구조를 무시하고 새 프레임워크를 도입하는 것
- 크롤러/ML/알림 기능을 한 파일에 몰아넣는 것
- 테스트 없이 핵심 계산 로직만 바꾸는 것
- `.env` 없이 시크릿 값을 코드에 직접 넣는 것
- 현재 placeholder 구현을 제거만 하고 대체 구현 없이 끝내는 것

## 추천 작업 순서

1. `app/ai/price_prediction.py`와 predictor 계층 위에 backtest / predictor accuracy 집계를 설계
2. 모델 정확도 / fallback / guardrail 집계를 `app/api/analytics.py`에 추가
3. `docker-compose.yml`, `app/tasks/`를 정리해 무거운 작업을 운영 경로로 분리
   - 기본 compose는 `api-runtime`
   - semantic/embedding 재색인은 `api-embedding`
   - 학습/데이터셋 정리는 `api-training`
4. WebSocket 실시간 이벤트 레이어를 추가하고 notification payload를 정규화
5. `app/services/decision_experiments.py`에서 threshold 외 실험 결과 반영 범위를 workload/category까지 확장

## 실행 전 확인 체크리스트

- `.env`가 존재하는가
- PostgreSQL 연결 정보가 유효한가
- 새 의존성이 `requirements.txt`에 반영되었는가
- 테스트가 추가되었는가
- README 또는 관련 운영 문서가 업데이트되었는가

## 참고 문서

- 기획 원본: `first_plan.md`
- 프로젝트 지침: `.github/copilot-instructions.md`
- 운영 개요: `README.md`
