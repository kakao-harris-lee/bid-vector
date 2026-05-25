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
- 현재 검증 상태: `python3 -m py_compile app/services/notifications/telegram_strategy.py app/services/notifications/update_processor.py app/api/operator.py app/api/operations.py app/api/analytics.py app/schemas/schemas.py` 통과, `pytest -q tests/test_operator.py tests/test_operations.py` 기준 `76 passed`, 로컬 `pytest -q` 기준 `164 passed, 1 skipped`, `docker compose config --quiet` 및 `docker compose --profile tasks config --quiet` 통과
- Docker 이미지 프로필: `api-runtime`(기본), `api-embedding`, `api-training`, `api-ml-full`

### 현재까지 완료된 범위

- 인증 / 운영자 프로필 / 전략 설정 API
- 프로젝트 / 입찰 CRUD 및 운영자 overview / notification 조회
- 나라장터 수집 mock/live 경로, `CrawlJob` / `HistoricalData` / `TenderResult` 적재, `Project` 연결
- 규칙 기반 + 의미 기반 분류, pgvector 기반 유사 공고 검색
- 가격 예측 데이터셋 추출, 통계 기반 예측, feedback calibration, reserve pattern, guardrail 응답 필드
- persisted prediction metadata 기반 predictor / fallback / guardrail / linked-result accuracy observability API
- `PRICE_PREDICTION_PREFERRED_PREDICTOR=auto` 기반 rolling backtest predictor selection
- predictor abstraction 및 `HistoricalStatisticalPredictor`, artifact-backed `LSTMBidRatePredictor` / `EnsembleBidRatePredictor` 추론
- opportunity analysis / bid recommendation / persisted bid decision engine
- `BidDecisionRecord` 기반 우선순위 / 추진 결정, score breakdown, margin / complexity / workload 반영
- 결정 상세 / 타임라인 / 퍼널 / 트렌드 / 세그먼트 / 기간 비교 / 추천 analytics
- 추천을 실험 계획으로 확장한 `decision-recommendations`
- 실험 실행 이력 / 평가 API (`decision-experiments`) 및 baseline vs current 비교
- 실험 run 수동 상태 변경 / 메모 갱신 / threshold, workload, category 적용 feedback loop
- 실험 run별 `application_status`, `application_history`, `next_actions` 기반 dashboard action payload
- 성공 / 실패 / 보류 실험 이력 기반 review bucket, priority sort, 필터/카운트 payload
- experiment run 이력 기반 `decision-recommendations` priority score / history adjustment
- 장기 experiment run 이력 기반 category/threshold `parameter_recommendation`, confidence, apply-time delta scaling
- Telegram 알림 / callback / polling / 상태 동기화, `/strategy` 버튼 기반 단계형 전략 편집, 웹 알림 fallback, 인증된 WebSocket realtime event stream
- 웹 클라이언트용 operator dashboard API 계약: `GET /api/v1/operator/dashboard`의 `cards`, `recent_decisions`, `recent_monitor_runs`, `feedback_summary`, `action_hrefs`
- PostgreSQL `LISTEN/NOTIFY` 기반 optional realtime fanout backend
- 전략 모니터링 preview / execute / history 및 in-process scheduler
- 크롤 성공률 / 전략 모니터링 성과 / 최근 실패 원인을 묶은 operations dashboard analytics
- operations dashboard의 task/broker 진단 payload, queue route 진단, stale/failed/retry task 집계 및 task health 카드
- operations dashboard의 Telegram 전송률/실패 원인, ML release manifest/signature/promotion gate/backtest 카드
- price predictor training run의 dataset 품질 리포트, artifact 비교 리포트, manifest promotion gate 입력 연결
- predictor promotion gate의 standard/canary/strict/advisory policy preset 및 dataset quality gate reason
- `docker compose` 복구, healthcheck/env wiring 정리, CPU-only PyTorch + pip cache 기반 Docker build 최적화
- runtime / embedding / training / dev 의존성 분리 및 멀티타깃 Docker build 정리
- manifest 기반 ML artifact promotion 서비스/CLI 및 embedding rebuild 자동화
- manifest 추천값의 `.env` 반영 자동화 (`--write-env-file`)
- manifest apply 후 compose 재기동 + health 확인 + API 기반 embedding rebuild rollout 자동화
- ML release rollout preflight: manifest signature required 모드, artifact 경로, file/S3 object storage credential/IAM write probe, 실패 원인 payload
- Celery worker가 `app.tasks.jobs`를 명시적으로 import 하도록 정리해 out-of-process worker/beat 경로에서 task 등록 누락을 방지
- 외부 broker 사용 시 `CELERY_RESULT_BACKEND` 기본값을 PostgreSQL(`db+${DATABASE_URL}`)로 자동 승격하도록 정리
- optional `tasks` compose profile(`rabbitmq`, `worker`, `beat`) 및 관련 Makefile/문서 경로 추가

### 아직 핵심적으로 남은 범위

- 실제 KONEPS/Telegram credential 환경에서 수집 → 전략 후보 → 모니터링 → Telegram 알림/버튼 callback → 전략 명령/버튼 편집까지 한 주기 smoke test
- 운영 배포 환경에서 `preflight-rollout`을 실제 object storage credential/IAM으로 실행
- 별도 프론트엔드 저장소가 있다면 `GET /api/v1/operator/dashboard` 계약을 화면 컴포넌트에 연결

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
- operations dashboard의 crawl 상태 카드와 실패 원인 집계

남은 작업:

- live 수집 안정화(retry, backoff, selector drift 대응)
- 백필/주기 수집 작업 분리
- 실제 KONEPS OpenAPI credential로 운영 smoke test 실행 및 증적 저장

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
- rolling backtest 기반 `auto` predictor selection
- predictor observability API와 기간 버킷 성능 추세
- release manifest predictor promotion gate
- training 산출물 dataset 품질 리포트 및 artifact 비교 리포트
- release policy preset 기반 promotion gate threshold 및 dataset quality status 검증

남은 작업:

- 운영 배포 시 `preflight-rollout`을 실제 credential/IAM 환경에서 실행해 bucket/prefix 권한을 확인

우선 검토 파일:

- `app/services/prediction_dataset.py`
- `app/ai/price_prediction.py`
- `app/ai/predictors/base.py`
- `app/ai/predictors/historical.py`
- `app/ai/predictors/lstm.py`
- `app/ai/predictors/ensemble.py`
- `tests/test_prediction_predictors.py`
- `tests/test_predictions.py`

### D. 입찰 추진 결정 / 실험 분석 — 핵심 완료, 운영 자동화 확장 단계

이미 구현됨:

- `BidDecisionRecord` 기반 추진 결정 엔진과 영속화
- 상세 / 타임라인 / 퍼널 / 추천 analytics
- recommendation → experiment plan 확장
- 실험 run 저장 / baseline snapshot / evaluate API
- 실험 수동 종료 / 롤백 / 메모 수정 API
- 성공 실험의 threshold 적용 feedback loop
- workload auto-calibration / category focus-shift 실험의 operator strategy 적용 feedback loop
- 적용 가능/완료/차단 상태와 action payload를 포함한 실험 응답
- 성공 / 실패 / 보류 이력 기반 review bucket, 우선순위 정렬, outcome/application 필터와 집계 payload
- 장기 experiment run 이력을 반영한 recommendation ranking, 성공 적용 boost, 반복 실패/보류 감점
- 장기 적용 결과를 반영한 category/threshold 세부 `parameter_recommendation` 및 threshold/category apply delta scaling

남은 작업:

- 운영 데이터 기반 threshold/category recommendation calibration 지속

우선 검토 파일:

- `app/services/allocation.py`
- `app/services/decision_analytics.py`
- `app/services/decision_experiments.py`
- `app/api/analytics.py`
- `app/schemas/schemas.py`

### E. 알림 채널 — Telegram / WebSocket 기반 완료, 이벤트 범위 확장 단계

이미 구현됨:

- Telegram 메시지 포맷 / callback / polling / 상태 동기화
- `/strategy`, `/strategy_set`, `/strategy_clear` 기반 전략 조회/수정/초기화
- `/strategy` inline button 기반 단계형 편집
  - 지원 필드: 업종, 지역, 키워드, 예산, 임계치, 알림 범위, 후보 수
  - 흐름: 필드 선택 → 새 값 입력 → 검증 → 적용/취소
  - 잘못된 입력은 기존 전략을 변경하지 않고 현재 값과 예시를 안내
  - pending edit state는 API multi-worker 환경을 고려해 internal analytics telemetry로 보존
- 웹 알림 fallback 및 운영자 notification 조회
- WebSocket 연결 관리 레이어 및 realtime event envelope
- WebSocket operator access token 인증
- optional PostgreSQL `LISTEN/NOTIFY` fanout backend 및 lifecycle start/stop
- 추천 / 투찰 / 크롤 / 전략 모니터링 완료·실패 이벤트 브로드캐스트

남은 작업:

- 실제 Telegram bot credential/chat id/webhook 또는 polling 환경에서 전략 명령/버튼 편집 smoke test
- 필요 시 이벤트 범위 확대 및 frontend reconnect/replay 정책 조율

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
- ML backfill / training / decision experiment re-evaluation API는 task id만 반환하고 실제 실행을 worker queue로 분리
- release manifest 서명, local retention/archive, optional file/S3 object storage publish 경로 구현

남은 작업:

- 운영 배포 시 `preflight-rollout`을 실제 credential/IAM 환경에서 실행

### H. 웹 프론트엔드 (Vite + React + TS) — Claude Code와 협업 확장 진행 중

이미 구현됨:

- `frontend/` 단일 SPA: 로그인/패스워드 리셋, 요약 대시보드, 입찰/결과 리스트, paper-bidding 요약 카드 (`App.tsx` 1,150 LOC, `api.ts`, `types.ts`, `styles.css`)
- 백엔드 `/dashboard` 라우트에서 빌드 산출물 서빙 (`app/main.py`)
- Vitest + Testing Library 셋업 및 App-level smoke test

진행 중 / 남은 작업 (스프린트 단위, 자세한 내용은 `docs/web-development-plan.md`):

- Phase 0 — Tailwind + shadcn/ui + `react-router-dom` + `@tanstack/react-query` 기반 정비, `App.tsx`를 `features/`로 분할
- Phase 1 — 운영자 전략 편집 UI (`OperatorStrategy` 폼/칩, candidates preview 동시 갱신)
- Phase 2 — 공고 탐색 UI (리스트/상세/유사공고, 임베딩 재계산)
- Phase 3 — 결정 게이트웨이 UI (decision funnel/recommendations, 상태 전환)
- Phase 4 — 실험 lifecycle UI (`decision_experiments` apply/rollback, dry-run 표시)
- Phase 5 — **가상 운영자 백테스트 비교 대시보드** + 신규 백엔드 API (`app/api/synthetic.py`, `app/services/synthetic_backtest.py`, `tasks/jobs.run_synthetic_operator_backtest`)
- Phase 6 — 운영 대시보드 카드 시각화 (`/api/v1/analytics/operations-dashboard`)
- Phase 7 — WebSocket realtime + 알림 드로어 + Playwright happy-path E2E + `styles.css` 폐기 완료

협업 모델 (자세한 내용은 `CLAUDE.md`):

- 서브에이전트: `frontend-builder` / `backend-builder` / `api-reviewer` / `test-runner` / `data-seed-runner`
- 슬래시 커맨드: `/screen`, `/api-route`, `/sync-types`, `/run-backtest`, `/seed-synthetic`, `/check`
- MCP: PostgreSQL(dev, read-only), Filesystem, (옵션) KONEPS mock, Telegram (dev token일 때만)

우선 검토 파일:

- `frontend/src/App.tsx` (분할 대상)
- `frontend/src/api.ts`, `frontend/src/types.ts`
- `frontend/src/styles.css` (점진 폐기 대상)
- `docs/web-development-plan.md`
- `CLAUDE.md`

### G. 분석 / 대시보드 집계 — prediction / operations / operator dashboard 계약 완료, 외부 클라이언트 연결 단계

이미 구현됨:

- overview / summary / prediction feedback
- operator dashboard: `GET /api/v1/operator/dashboard`
  - `cards`: 프로필, 진행 중 판단, 미확인 알림, 모니터링 실패, 추천 오차율
  - `recent_decisions`: 최근 입찰 판단과 `/api/v1/operations/bid-decisions/{id}` 링크
  - `recent_monitor_runs`: 최근 전략 모니터링 실행과 `/api/v1/operator/strategy/monitor/runs/{run_id}` 링크
  - `feedback_summary`: prediction feedback 요약과 `/api/v1/analytics/prediction-feedback` 링크
  - `action_hrefs`: 분석, 후보, 모니터링, 운영 대시보드 진입점
- OpenAPI/response model 기반 operator dashboard contract test 및 빈 상태 테스트
- prediction observability: predictor별 정확도, fallback 빈도, guardrail 빈도, pricing mode breakdown, 기간 버킷 성능 추세
- operations dashboard: 크롤 성공률, 실패 원인, 전략 모니터링 완료율, 후보 선택/저장/알림 비율, task/broker health, stale/failed/retry task 집계, Telegram 전송률, ML release gate/backtest 상태
- decision insights / funnel / recommendations / experiments

남은 작업:

- 별도 프론트엔드 앱이 있다면 operator dashboard API 계약을 실제 화면 컴포넌트에 연결
- 실제 KONEPS/Telegram 외부 연동 환경에서 운영 smoke test 결과를 문서화
- 실제 운영 credential/IAM 기준 object storage rollout preflight 실행 결과를 운영 절차에 반영

## 현재 권장 실행 순서

1. 웹 프론트엔드 Phase 0 시작 — `docs/web-development-plan.md`의 기반 정비 작업
2. 백엔드 Phase 5 신규 API(synthetic backtest) 설계는 Phase 4 후반에 병렬로 시작
3. 실제 KONEPS/Telegram credential 환경에서 운영 smoke test 실행
4. 운영 배포 환경에서 `preflight-rollout`을 실제 object storage credential/IAM으로 실행
5. 필요 시 A/B 분류 정확도 보정 또는 realtime replay/retention 정책 정리

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

1. 운영 smoke test는 `docs/remaining-priority-plan.md`의 KONEPS/Telegram 실행 순서와 증적 기준을 따른다.
2. ML release preflight는 `make ml-release-preflight MANIFEST_REF=<manifest-ref> REQUIRE_SIGNATURE=true` 또는 `python scripts/promote_ml_release.py preflight-rollout --manifest <manifest-ref> --require-signature`로 실행한다.
3. predictor backtest / `auto` selection은 `app/ai/predictor_backtest.py`와 `app/ai/price_prediction.py`에 구현됨
4. 모델 정확도 / fallback / guardrail / 기간 추세 집계는 `app/api/analytics.py`의 `prediction-observability`로 제공 중
5. `docker-compose.yml`, `app/tasks/`를 정리해 무거운 작업을 운영 경로로 분리
   - 기본 compose는 `api-runtime`
   - semantic/embedding 재색인은 `api-embedding`
   - 학습/데이터셋 정리는 `api-training`
6. WebSocket 실시간 이벤트 레이어와 notification/crawl/strategy event payload는 구현됨
7. `app/services/decision_experiments.py`의 action payload는 구현됐고, 다음은 성공/실패/보류 실험 이력 기반 정렬 보강

## 실행 전 확인 체크리스트

- `.env`가 존재하는가
- PostgreSQL 연결 정보가 유효한가
- 새 의존성이 `requirements.txt`에 반영되었는가
- 테스트가 추가되었는가
- README 또는 관련 운영 문서가 업데이트되었는가

## Git/PR 워크플로 (모든 비-trivial 작업 필수 순서)

**작업 시작 전부터 머지까지 반드시 아래 순서를 지킵니다.** 글로벌 `~/.claude/CLAUDE.md`의 `MANDATORY WORKFLOW`, 프로젝트 `CLAUDE.md` 섹션 11과 같은 규칙입니다.

```
main 확인 → feature branch 생성 → 작업/커밋 → push → PR 생성 → /code-review → 리뷰 대응 → 머지
```

### 단계

1. `git status` / `git log -n 10`로 `main`이 `origin/main`과 동기인지 확인합니다.
2. `git switch -c <type>/<slug>`로 새 브랜치를 만든 뒤 작업을 시작합니다. `main`에 직접 커밋하지 않습니다.
3. atomic 단위로 커밋하고, `pytest -q && npm --prefix frontend run test && npm --prefix frontend run build`(또는 `/check`)로 회귀를 검증합니다.
4. `git push -u origin <branch>` → `gh pr create --base main --head <branch> --title ... --body ...`로 PR을 엽니다.
   - PR 본문: 무엇/왜/테스트 한 방법/수용 기준 체크리스트 포함.
5. PR을 연 직후 `/code-review`(또는 `/code-review:code-review`)를 실행해 자동 리뷰를 받습니다.
6. 받은 코멘트는 같은 브랜치에 추가 커밋 + push로 대응합니다. 회귀 방지 테스트도 같이 작성합니다.
7. 모든 체크가 green이고 리뷰가 처리된 뒤, **사용자가 "merge"/"land"라고 명시했을 때만** 머지합니다.

### 브랜치 네이밍

- `feature/<slug>` — 신규 기능/화면/라우트
- `fix/<slug>` — 버그 수정
- `chore/<slug>` — 의존성·빌드·CI
- `docs/<slug>` — 문서만 변경
- `refactor/<slug>` — 동작 변경 없는 리팩토링

### 예외 (브랜치/PR 없이 직접 푸시 가능)

다음만 예외입니다. 의심되면 항상 PR 경로로 진입합니다.

- 문서 오타 1줄 수정
- 명백히 자명한 lint/format fix
- 사용자가 명시적으로 "PR 없이 직접 푸시"를 지시했을 때

### 이미 main에 커밋된 경우 복구

```bash
git switch -c feature/<slug>          # 현재 HEAD에서 분리
git switch main
git reset --keep origin/main          # main을 origin과 동기화
git switch feature/<slug>             # 작업 계속
git push -u origin feature/<slug>
gh pr create ...
```

### 다중 작업 동시 진행

여러 작업은 각 작업당 별도 브랜치/PR로 분리합니다. 한 브랜치에 무관한 변경을 섞지 않습니다.

## 참고 문서

- 기획 원본: `first_plan.md`
- 프로젝트 지침: `.github/copilot-instructions.md`
- 운영 개요: `README.md`
- 운영 smoke test 간단 실행 가이드: `docs/production-smoke-test.md`
- 현재 잔여 과제/운영 증적 기준: `docs/remaining-priority-plan.md`
- 구현 로드맵 상태: `docs/optimal-bid-analysis-roadmap.md`
- first_plan 대비 현재 구현 매핑: `docs/first_plan_implementation_review.md`
- 웹 프론트엔드 확장 계획: `docs/web-development-plan.md`
- Claude Code 협업 가이드: `CLAUDE.md`
