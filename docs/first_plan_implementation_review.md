# first_plan.md 대비 현재 구현 현황 정리

작성일: 2026-05-25
대상: `bid-vector` 저장소 전체 (`app/`, `frontend/`, `scripts/`, `docs/`, `tests/`)

## 1. 총평

`first_plan.md`에서 제시한 5개 핵심 모듈(크롤러 / 분류기 / 브레인(AI) / 인터페이스 / DB)은
모두 실제 코드로 구현되어 동작 가능한 상태이며, 기획안의 골격을 **대체로 충실히 따르되**
다음 두 방향으로 크게 확장되었습니다.

1. **단일 운영자(single-operator) 모델로 전환** — first_plan의 "여러 사용자에게 낙찰가를 공정 분배"
   로직은 운영 현실(현 시점 1개 업체 기준)에 맞춰 단일 운영자 결정 엔진
   (`BidDecisionService`, `BidDecisionRecord`)으로 재설계됨.
2. **운영/관측/실험 레이어 대폭 추가** — 백테스트(`paper_bidding`), 실험 기반 전략 튜닝
   (`decision_experiments`), 모델 릴리즈 파이프라인(`ml_release`), 오퍼레이션 대시보드 등
   원안에 없던 운영 자동화 기능이 두텁게 붙음.

## 2. 모듈별 매핑 표

| first_plan 모듈 | 기획 요구사항 | 현재 구현 위치 | 충족도 | 비고 |
|---|---|---|---|---|
| 모듈 1: 크롤러 | Playwright/BS4, 공고/개찰결과/15개 복수예가/4개 선택번호, anti-bot | `app/services/koneps/collector.py` (2,886 LOC), Playwright 1.52, BS4, requests | ✅ 완료 (+확장) | 기획의 라이브 크롤 외에 **나라장터 OpenAPI(`koneps-openapi`, `scsbid-openapi`)를 1차 경로로 사용**하고 라이브 크롤은 fallback. `HistoricalData.reserve_prices`(15개), `selected_numbers`(4개) 컬럼 그대로 적재 |
| 모듈 2: 분류기 | sentence-transformers, 의미 유사도 ≥ 85%, 면허/지역/시공능력 매칭 | `app/services/classifier.py` (717 LOC), `app/services/project_similarity.py`, `Project.embedding` (pgvector(384)) | ✅ 완료 | 규칙(룰) + 의미(임베딩) 하이브리드. 임베딩 모델은 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 다운로드 완료(`models/`) |
| 모듈 3: 브레인(AI) | LSTM/Ensemble + T-분포 보정, 공정 분배, 투찰금액 공식 | `app/ai/predictors/{historical,lstm,ensemble,base}.py`, `app/ai/price_prediction.py`, `app/services/allocation.py` | ⚠️ 부분 변경 | predictor 추상화 / 백테스트 자동 선택까지 구현. **"공정 분배"는 단일 운영자 결정 엔진으로 의미가 바뀜** |
| 모듈 4: 인터페이스(Telegram) | python-telegram-bot, 인라인 [상세/투찰완료/관심제외] | `app/services/notifications/{telegram,telegram_strategy,update_processor,manager}.py` | ✅ 완료 (+확장) | 인라인 버튼 콜백, 폴링, 단계형 `/strategy` 편집, 웹 알림 fallback, 인증된 WebSocket 실시간 스트림까지 |
| 모듈 5: DB | Bids / Historical_Data / Users / Allocations | `app/models/models.py` (19개 모델) | ✅ 완료 (+대폭 확장) | 기획 4개 테이블에 더해 회사 프로필, 운영자 전략, 결정 기록, 실험 이력, 페이퍼 입찰/정산, 크롤잡, 문서분석, 알림, 분석 등 다수 추가 |

## 3. 모듈별 상세 구현 현황

### 모듈 1 — 나라장터 실시간 크롤러
- 코드: `app/services/koneps/collector.py`, 적재 모델 `CrawlJob`, `HistoricalData`, `TenderResult`,
  `Project` 연결. 실시간 fanout은 `app/services/realtime.py`로 분리.
- 데이터 소스: KONEPS OpenAPI(공고: `koneps-openapi` 계열, 낙찰결과: `scsbid-openapi` 계열)를 우선 사용하고
  실패 시 라이브 크롤(`requests` + `BeautifulSoup`)로 fallback. 의존성에 `playwright==1.52.0` 포함.
- 에러 처리: `KonepsLiveCollectionError`로 stage/category/attempt 메타데이터를 보존하고,
  `format_crawl_error_message`로 CrawlJob에 카테고리화된 실패 사유 기록. (first_plan의 "재시도 + 관리자 알림" 충족)
- 보조 스크립트: `scripts/crawl_koneps_categories.sh`, `scripts/crawl_koneps_awards.sh`.

### 모듈 2 — 맞춤형 공고 분류기
- 룰 베이스: 업무구분(software/technical-service/service/goods/construction/other) 일치/관련성, 면허 코드,
  지역(전국·서울·부산·… 별칭 매핑), 예산/시공능력/역량 점수, semantic strong/good/base 점수와
  미스매치 페널티가 명확하게 가중치화되어 있음.
- 임베딩: `Project.embedding`이 `VECTOR(384)`(pgvector), `embedding_payload`/`embedding_model`/`embedding_updated_at`
  메타 컬럼 분리. `project_similarity.py`가 임베딩 적재/유사 공고 검색을 담당.
- 임계값: `MATCH_THRESHOLD = 0.65` (first_plan의 85%는 의미적 강매칭(`SEMANTIC_STRONG_SCORE`) 영역으로 반영).

### 모듈 3 — 데이터 엔진 / AI 분석
- predictor 추상화: `BasePricePredictor`, `PricePredictionContext`, `PredictorAvailability`.
- 구현체 4종:
  - `HistoricalStatisticalPredictor` — 통계 기반 기본 predictor, T-분포 보정과 reserve pattern을 활용.
  - `LSTMBidRatePredictor` — 영구화된 LSTM artifact(JSON) 기반 추론, 표본 부족 시 가용성 차단.
  - `EnsembleBidRatePredictor` — 위 둘을 블렌딩.
  - `PRICE_PREDICTION_PREFERRED_PREDICTOR=auto`일 때 rolling backtest로 자동 선택.
- guardrail: `price_prediction.py`의 `_apply_prediction_guardrails`가 카테고리별 낙찰하한율을 적용,
  결과를 `guardrail_applied / guardrail_reason / floor_bid_rate / floor_price` 컬럼에 영속화
  → **first_plan의 "AI가 계산한 투찰가가 낙찰하한 미만으로 내려가지 않도록" 요구사항을 충족**.
- 결정 엔진: `BidDecisionService` (allocation.py)가 확률/매칭/긴급도/경쟁/예산캡처/마진 가중합과
  워크로드·복잡도 페널티로 우선순위 점수를 계산, `BidDecisionRecord`에 결정 상태(planned/reviewing/submitted/skip)
  와 사유, 스코어 분해를 영속화.
- 공정 분배: first_plan의 "낙찰이 적은 사용자에게 우선 배정" 로직은 운영 현실에 맞춰 단일 운영자의
  pursue/skip 결정 + 워크로드 캡(`current_active_bids`/`max_active_bids`) 페널티로 대체됨.
  이 변화는 `AGENT.md`의 운영 원칙과 일치.

### 모듈 4 — 텔레그램 인터페이스
- 메시지 송신: `TelegramNotificationService` — 환경 미구성 / 테스트 환경 분기, `sendMessage` / `answerCallbackQuery`.
- 인라인 버튼: `telegram_strategy.py`, `update_processor.py`가 [투찰완료/검토/보류] 콜백을 수신해
  `BidDecisionRecord` 상태와 `reasoning` 노트(`TELEGRAM_SUBMITTED_NOTE` 등)에 반영.
- 추가 구현: `/strategy` 단계형 편집, 웹 알림 fallback, 인증된 WebSocket 실시간 푸시(`realtime.py`).

### 모듈 5 — 데이터베이스
| 테이블 (19개) | 역할 |
|---|---|
| `users`, `company_profiles`, `operator_strategies`, `operator_strategy_runs` | 운영자 계정/프로필/전략/실행 이력 |
| `projects` | 공고 + 임베딩 (`VECTOR(384)`) |
| `bids` | 실제 입찰 기록 |
| `price_predictions` | 예측 결과 + predictor 메타 + guardrail |
| `historical_data` | 과거 사정률/15개 예가/4개 번호 |
| `bid_decision_records` | 결정 엔진의 점수/사유/상태 영속화 |
| `decision_experiment_runs` | 전략 튜닝 실험 이력 |
| `allocations` | (legacy) 다중 사용자 배분 테이블 — 도메인 마이그레이션 잔재 |
| `tender_results` | 실제 낙찰 결과 스냅샷 |
| `paper_bid_runs`, `paper_bids`, `paper_bid_settlements` | 페이퍼 입찰 백테스트/정산 |
| `crawl_jobs` | 크롤 실행 이력 |
| `document_analyses`, `notifications`, `analytics` | 부가 분석/알림/이벤트 |

## 4. first_plan에 없던 추가 구현

이 부분이 현재 저장소의 **실질적 가치 대부분을 차지**합니다.

- **백테스트 파이프라인** — `app/services/paper_bidding_backtest.py`, `paper_bidding_scheduler.py`,
  `scripts/backtest_*.py`, `app/ai/predictor_backtest.py`. 과거 데이터로 predictor·결정 엔진 성능 검증.
- **결정 분석/실험** — `decision_analytics.py`, `decision_experiments.py`, `operator_strategy_tuning.py`.
  카테고리·임계값·워크로드 페널티를 실험 단위로 추적·평가·반영.
- **운영 대시보드** — `app/api/operations.py`, `app/api/dashboard.py`,
  `app/services/analytics_reporting.py`. 크롤 성공률, 모니터링 성과, Telegram 전송률,
  ML release manifest/promotion gate, 큐/태스크 헬스 카드까지 단일 dashboard payload로 노출.
- **ML 릴리즈 자동화** — `app/services/ml_release.py`, `scripts/promote_ml_release.py`.
  manifest signature, 오브젝트 스토리지 preflight, `.env` 자동 반영, embedding 재빌드, compose 재기동까지 일괄.
- **분리 의존성 / 멀티 프로필 Docker** — `requirements/{runtime,ml-embedding,ml-training,dev}.txt`,
  Docker 이미지 프로필 `api-runtime / api-embedding / api-training / api-ml-full`.
- **별도 프론트엔드** — `frontend/` (Vite + React + TypeScript), `/dashboard` SPA 라우트가
  `app/main.py`에서 서빙. (first_plan은 텔레그램 한 채널만 가정)
- **태스크 런타임** — `app/tasks/{celery_app,jobs}.py` + optional `tasks` compose profile
  (RabbitMQ/worker/beat).

## 5. first_plan 요구사항 충족 체크리스트

- [x] Mac/Python 3.10+ 환경 가정, 모듈별 디렉토리 분리 — `app/{api,ai,core,models,schemas,services,tasks}/`
- [x] Playwright + BeautifulSoup4 의존성
- [x] 공고번호·기초금액·마감일·업종/지역 제한 추출 → `Project` + `HistoricalData`
- [x] 15개 복수예가 + 4개 선택번호 적재 → `HistoricalData.reserve_prices`, `selected_numbers`
- [x] sentence-transformers 임베딩 + 의미 유사도 분류 → `paraphrase-multilingual-MiniLM-L12-v2` + pgvector
- [x] 업무구분/면허/지역/시공능력 필터 → `NoticeClassifierService` 규칙
- [x] LSTM 기반 시계열 학습 → `LSTMBidRatePredictor` (영구화 artifact 추론, 학습 스크립트 별도)
- [x] T-분포 보정/소표본 안전성 → `HistoricalStatisticalPredictor`
- [x] 투찰금액 공식 = 기초금액 × 예측사정률 × 낙찰하한율 → `price_prediction.predict_price` + guardrail
- [x] Telegram 인라인 버튼 [상세/투찰완료/관심제외] → callback + `BidDecisionRecord` 상태 동기화
- [x] DB: Bids / Historical_Data / Users / Allocations → 모두 존재(+ 13개 추가 테이블)
- [x] `.env` 기반 시크릿 관리 — `.env`, `.env.example` 존재
- [x] Human-in-the-loop verification — predictor guardrail + 결정 엔진 review 상태
- [x] 크롤링 실패 재시도 + 관리자 알림 — `KonepsLiveCollectionError`, ops dashboard 실패 원인 집계
- [~] **공정 분배 로직(다중 사용자에 최상위 확률 우선 배정)** — 단일 운영자 결정 엔진으로 의미 전환됨.
  `allocations` 테이블이 legacy로 남아 있어 다중 운영자 확장 시 자연스럽게 복원 가능

## 6. AGENT.md가 명시한 "아직 핵심적으로 남은 범위"

(저장소 메모와 일치, 참고용)

1. 실제 KONEPS/Telegram credential 환경에서 수집 → 전략 후보 → 모니터링 → 텔레그램 알림/콜백 →
   전략 명령/버튼 편집까지 한 주기 end-to-end 스모크 테스트
2. 운영 배포 환경에서 `preflight-rollout`을 실제 object storage credential/IAM으로 실행
3. 별도 프론트엔드 저장소(있다면)에 `GET /api/v1/operator/dashboard` 계약 화면 연결

## 7. 결론

- **first_plan.md의 골격은 충실히 구현되어 있고**, 핵심 모듈(크롤러·분류기·예측·텔레그램·DB)은 동작 가능한 형태로
  코드와 모델, 테스트가 모두 존재합니다. (`tests/test_*.py` 16개, 19개 SQLAlchemy 모델, 14개 API 라우트, 25개 도메인 서비스 모듈)
- 가장 큰 의미상 변경점은 "다중 사용자 공정 분배" → "단일 운영자 결정 엔진"으로의 전환입니다.
  legacy `allocations` 테이블이 남아 있어, 추후 다중 운영자 확장이 필요해지면 큰 구조 변경 없이 되살릴 수 있습니다.
- 남은 작업은 코드 부재가 아니라 **실제 외부 자격 증명을 갖춘 환경에서의 end-to-end 검증**과
  **프론트엔드 연결**이 중심입니다.
