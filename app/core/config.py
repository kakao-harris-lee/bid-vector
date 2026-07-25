"""Application configuration"""

from typing import List
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://postgres:password@localhost:5432/bid_vector_db"
)
DEFAULT_CELERY_BROKER_URL = "memory://"
DEFAULT_CELERY_RESULT_BACKEND = "cache+memory://"


def _to_celery_database_result_backend(database_url: str) -> str:
    """Translate a SQLAlchemy URL into Celery's database backend format."""
    normalized_database_url = (database_url or "").strip()
    if not normalized_database_url:
        return DEFAULT_CELERY_RESULT_BACKEND
    if normalized_database_url.startswith("db+"):
        return normalized_database_url
    return f"db+{normalized_database_url}"


class Settings(BaseSettings):
    """Application settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 3000
    DEBUG: bool = True
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = (
        "postgresql+psycopg://postgres:password@localhost:5432/bid_vector_db"
    )
    DATABASE_USER: str | None = None
    DATABASE_PASSWORD: str | None = None
    DATABASE_HOST: str | None = None
    DATABASE_PORT: int | None = None
    DATABASE_NAME: str | None = None

    # JWT
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    OPERATOR_PASSWORD_RESET_TOKEN: str = ""

    # Background jobs
    CELERY_BROKER_URL: str = DEFAULT_CELERY_BROKER_URL
    CELERY_RESULT_BACKEND: str = DEFAULT_CELERY_RESULT_BACKEND
    CELERY_TASK_DEFAULT_QUEUE: str = "bid_vector"
    CELERY_OPS_QUEUE: str = "bid_vector_ops"
    CELERY_ML_BACKFILL_QUEUE: str = "bid_vector_ml_backfill"
    CELERY_ML_TRAINING_QUEUE: str = "bid_vector_ml_training"
    CELERY_ML_REEVALUATION_QUEUE: str = "bid_vector_ml_reevaluation"
    CELERY_ALLOW_INLINE_ML_TASKS: bool = False
    CELERY_WORKER_CONCURRENCY: int = 2
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = 100
    CELERY_TASK_TIME_LIMIT_SECONDS: int = 1800
    CELERY_TASK_SOFT_TIME_LIMIT_SECONDS: int = 1500
    # Max project ids per deferred-embedding backfill task. Bounds each
    # rebuild_project_embeddings run so a large catch-up sweep is split across
    # several tasks instead of one unbounded task that re-creates the time-limit
    # redelivery loop on the ML backfill queue.
    EMBEDDING_BACKFILL_CHUNK_SIZE: int = 200
    CELERY_RESULT_EXPIRES_SECONDS: int = 86400
    CELERY_TASK_TRACK_STARTED: bool = True
    CELERY_WORKER_SEND_TASK_EVENTS: bool = True
    CELERY_TASK_SEND_SENT_EVENT: bool = True
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    CELERY_BROKER_CONNECTION_MAX_RETRIES: int = 100
    CELERY_BROKER_PUBLISH_MAX_RETRIES: int = 3
    REALTIME_REQUIRE_AUTH: bool = True
    REALTIME_FANOUT_BACKEND: str = "local"
    REALTIME_POSTGRES_CHANNEL: str = "bid_vector_realtime_events"
    REALTIME_HISTORY_LIMIT: int = 100
    REALTIME_LISTENER_RECONNECT_BACKOFF_SECONDS: float = 5.0
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED: bool = False
    OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES: int = 30
    OPERATOR_STRATEGY_MONITOR_RUN_ON_STARTUP: bool = False
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT: int = 10
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY: bool = True
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_MAX_ACTIVE_BIDS: int = 3
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_CURRENT_WORKLOAD_SCORE: float = 0.0
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_SAME_CATEGORY_ONLY: bool = True
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_SIMILAR_LIMIT: int = 3
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_MIN_SIMILARITY: float = 0.15
    # Scheduled strategy monitor scan bound — the scheduled (non-interactive)
    # monitor only loads and ML-analyses the N most deadline-imminent active
    # notices instead of the entire open-notice table. Without this bound a
    # broad-license operator passes the cheap watch filters on most rows, so the
    # expensive per-candidate analysis (price prediction + pgvector similarity)
    # runs hundreds of times and the scheduled task hangs for >12 minutes,
    # tripping the consumer timeout. The interactive preview/manual paths keep
    # their own bounding (PREVIEW_SCAN_*) / full-scan behavior; this setting only
    # bounds the periodic schedule. Clamped to [floor, ceiling] in code.
    OPERATOR_STRATEGY_MONITOR_SCHEDULE_SCAN_LIMIT: int = 150
    PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED: bool = False
    PAPER_BIDDING_FORWARD_RUN_ON_STARTUP: bool = False
    PAPER_BIDDING_FORWARD_INTERVAL_MINUTES: int = 1440
    PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT: int = 50
    PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY: str = ""
    PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO: str = "base"
    PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT: int = 80
    PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST: bool = True
    # Forward paper-bid settlement — sweep unsettled forward paper bids whose
    # deadline has passed and whose tender result now exists, then create the
    # matching PaperBidSettlement rows. Default OFF; opt-in via .env.
    FORWARD_SETTLEMENT_SCHEDULE_ENABLED: bool = False
    FORWARD_SETTLEMENT_INTERVAL_MINUTES: int = 720  # 12h
    FORWARD_SETTLEMENT_LIMIT: int = 200
    FORWARD_SETTLEMENT_PERSIST: bool = True
    # Award-result Telegram — periodically verify tracked real bids whose 개찰
    # result is now public (winner in TenderResult) and, once per bid, send the
    # operator a factual 적격/경쟁력 summary. Idempotent via
    # BidDecisionRecord.award_notified_at. Runs after the scsbid 6h collection so
    # freshly-published winners are picked up. Default OFF; opt-in via .env.
    AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED: bool = False
    AWARD_RESULT_NOTIFY_INTERVAL_MINUTES: int = 360  # 6h
    AWARD_RESULT_NOTIFY_LIMIT: int = 50
    HISTORICAL_BACKTEST_SCHEDULE_ENABLED: bool = False
    HISTORICAL_BACKTEST_INTERVAL_MINUTES: int = 1440  # 24h
    HISTORICAL_BACKTEST_LOOKBACK_DAYS: int = 30
    HISTORICAL_BACKTEST_SCHEDULE_LIMIT: int = 100
    HISTORICAL_BACKTEST_SCHEDULE_CATEGORY: str = ""
    HISTORICAL_BACKTEST_SCHEDULE_SCENARIO: str = "base"
    HISTORICAL_BACKTEST_SCHEDULE_HISTORY_LIMIT: int = 80
    HISTORICAL_BACKTEST_SCHEDULE_CUTOFF_HOURS: int = 2
    HISTORICAL_BACKTEST_SCHEDULE_SETTLE_ACTIONS: str = "bid_now,review"
    HISTORICAL_BACKTEST_SCHEDULE_PERSIST: bool = True
    # NOTE: the *_HOUR_KST schedule settings below are interpreted in KST, not
    # UTC. Celery beat runs with timezone="Asia/Seoul" and enable_utc=False
    # (see app/tasks/celery_app.py), so crontab(hour=N) fires at N:00 KST.
    # Internal timestamp storage stays UTC (app/core/time.py utc_now()); only
    # these operator-facing schedule hours are KST.
    SMOKE_TEST_SCHEDULE_ENABLED: bool = False
    SMOKE_TEST_HOUR_KST: int = 7   # 07:00 KST
    SMOKE_TEST_MINUTE: int = 0
    # G-2 candidate re-check — daily read-only sweep that measures when synthetic
    # operators regain biddable candidates as niche inventory recovers. Calls the
    # read-only ``preview_candidates`` (never the heavy strategy monitor) and
    # records a single ``g2_candidate_recheck`` analytics event for evidence.
    # Default OFF; opt-in via .env.
    G2_CANDIDATE_RECHECK_SCHEDULE_ENABLED: bool = False
    G2_CANDIDATE_RECHECK_HOUR_KST: int = 21  # 21:00 KST
    G2_CANDIDATE_RECHECK_MINUTE: int = 0
    # G-2 evidence collection — daily read-only snapshot that records the
    # per-operator G-2 evidence ledger (via AnalyticsReportingService) into a
    # single ``collect_g2_evidence`` analytics event so ``counted_days`` can
    # accumulate toward the G-2 exit review. Runs at 22:00 KST (after the 21:00
    # candidate re-check, before the 07:00 smoke test). Reads existing data only
    # and never runs monitors, writes operator data, or sends Telegram.
    # Default OFF; opt-in via .env.
    COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED: bool = False
    COLLECT_G2_EVIDENCE_HOUR_KST: int = 22  # 22:00 KST
    COLLECT_G2_EVIDENCE_MINUTE: int = 0
    COLLECT_G2_EVIDENCE_WINDOW_DAYS: int = 30
    COLLECT_G2_EVIDENCE_RECENT_LIMIT: int = 5
    # Daily ledger-based manifest-draft.json write. The collect_g2_evidence beat
    # task writes one manifest-draft.json per KST day for the target operators so
    # ``scripts/build_g2_exit_review.py`` can accumulate ``counted_days`` toward
    # the G-2 exit review without a human running scripts/collect_g2_evidence.py
    # every day. This draft's "pass" verdict is LEDGER-based (build_g2_evidence
    # _summary), NOT the CLI's live endpoint-scope check. The only extra write is
    # this local JSON file — no operator data, no execution, no external calls.
    G2_EVIDENCE_TARGET_OPERATOR_IDS: str = "19,20,25"  # comma-separated operator ids
    G2_EVIDENCE_WRITE_DAILY_DRAFT: bool = True
    G2_EVIDENCE_DAILY_DRAFT_DIR: str = "reports/g2-evidence/daily"
    G2_EVIDENCE_REQUIRED_DAYS: int = 7
    # Price-predictor ML training — weekly Celery-beat schedule (training-worker
    # executes via the ML training queue). Default OFF; opt-in via .env.
    # request_payload is left at None → trains the full dataset with task defaults
    # (limit 500, create_manifest=True, publish_remote=True).
    PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED: bool = False
    PRICE_PREDICTOR_TRAINING_SCHEDULE_DAY_OF_WEEK: int = 0  # 0=Sunday (crontab day_of_week)
    PRICE_PREDICTOR_TRAINING_SCHEDULE_HOUR_KST: int = 18    # 18:00 KST
    PRICE_PREDICTOR_TRAINING_SCHEDULE_MINUTE: int = 0
    # Comma-separated category/business-group scoped candidate retrains to run
    # alongside the global weekly retrain. Empty string disables scoped runs.
    PRICE_PREDICTOR_TRAINING_SCHEDULE_CATEGORIES: str = "construction,service,goods"
    KONEPS_COLLECTION_SCHEDULE_ENABLED: bool = False
    KONEPS_COLLECTION_INTERVAL_MINUTES: int = 60
    KONEPS_COLLECTION_SOURCE: str = "koneps-openapi"
    KONEPS_COLLECTION_CATEGORY: str = ""
    KONEPS_COLLECTION_CATEGORIES: str = ""  # comma-separated, e.g. "construction,service"
    KONEPS_COLLECTION_MAX_ITEMS: int = 50  # 기존값 유지, .env에서 500으로 덮어씀
    KONEPS_COLLECTION_EXECUTION_MODE: str = "auto"
    # KONEPS scsbid (개찰 결과) collection — independent beat schedule.
    # Re-uses the same collect_koneps_notices task but routes to the scsbid OpenAPI
    # path via request_payload.source. Default OFF; opt-in via .env.
    KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED: bool = False
    KONEPS_SCSBID_COLLECTION_INTERVAL_MINUTES: int = 360
    KONEPS_SCSBID_COLLECTION_SOURCE: str = "scsbid-openapi"
    KONEPS_SCSBID_COLLECTION_CATEGORY: str = ""
    KONEPS_SCSBID_COLLECTION_MAX_ITEMS: int = 50
    KONEPS_SCSBID_COLLECTION_EXECUTION_MODE: str = "auto"
    # scsbid award forward-coverage — multi-category date-window sweep params.
    KONEPS_SCSBID_COLLECTION_CATEGORIES: str = "construction,service,goods"
    KONEPS_SCSBID_COLLECTION_LOOKBACK_DAYS: int = 3
    KONEPS_SCSBID_COLLECTION_PAGE_SIZE: int = 100
    # Per-notice reserve-detail (예정가) fetch page size (ScsbidInfoService numOfRows).
    KONEPS_SCSBID_DETAIL_PAGE_SIZE: int = 100
    KONEPS_SCSBID_COLLECTION_MAX_PAGES: int = 30
    KONEPS_SCSBID_COLLECTION_RESERVE_DETAIL: bool = True
    # Skip the per-notice reserve-detail HTTP fetch for awards that already have a
    # non-empty reserve_prices row persisted. The reserve (예정가) of an opened
    # award is a settled, immutable value, so re-fetching it on every rolling
    # window sweep is pure waste; this keeps scheduled runs from re-paying the
    # per-notice HTTP cost and blowing past the RabbitMQ consumer ack timeout.
    KONEPS_SCSBID_REUSE_PERSISTED_RESERVE_DETAIL: bool = True
    # Throttle between scsbid OpenAPI page/category calls (seconds). 0 allowed in tests.
    KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS: float = 0.2
    # Throttle between deferred reserve-detail backfill HTTP calls (seconds), kept
    # SEPARATE from the page-pagination delay above so the backfill can run slower
    # without slowing collection's page sweep. ScsbidInfoService rate-limits the
    # reserve-detail endpoint harder than the award-list endpoint (HTTP 429 "API
    # token quota exceeded" persisted even at the ~2/s serial pace the collection
    # delay implied), so the backfill needs its own, slacker pace. 0 disables the
    # inter-call sleep (used in tests).
    KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS: float = 1.0
    # Defer the per-notice reserve-detail HTTP fetch out of the time-limited Celery
    # collection task into a bounded async backfill. The inline path issues one
    # reserve-detail call (each preceded by a throttle sleep) per non-settled award
    # — up to thousands per scheduled sweep — which blew past the Celery hard time
    # limit, SIGKILLed the task, and (with task_acks_late) spun a redelivery loop
    # that persisted 0 rows. With this on, the collection task only paginates the
    # award lists inline (cheap) and enqueues the reserve-detail fetches as
    # ``backfill_scsbid_reserve_detail`` tasks. Synchronous callers are unaffected.
    # Set false to restore the fully-inline behaviour.
    KONEPS_SCSBID_RESERVE_DETAIL_DEFER: bool = True
    # Max notices per deferred reserve-detail backfill task. Bounds each
    # backfill_scsbid_reserve_detail run so a large catch-up sweep is split across
    # several short tasks instead of one unbounded task that re-creates the
    # time-limit redelivery loop on the ops queue.
    KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE: int = 200
    # Minimum age (hours) of a notice's opening/closing datetime before its reserve
    # detail is deferred for backfill. ScsbidInfoService imposes a *rate* limit (HTTP
    # 429 "API token quota exceeded"), not a daily quota, and a just-opened notice
    # usually has no settled reserve yet — fetching it returns an empty reserve
    # ("not_settled") and is re-tried every 6h sweep, wasting calls against the rate
    # limit. Skipping notices whose opening is more recent than
    # ``now - MIN_SETTLE_AGE_HOURS`` defers each notice's single fetch until it has
    # had time to settle; within the 3-day lookback an aging notice is then fetched
    # exactly once. Notices with an unknown/None opening datetime are still deferred
    # (the gate cannot apply, so we try). 0 disables the gate.
    KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS: int = 24
    # Re-check backoff for permanently-empty notices. The age-gate above defers a
    # notice once its opening is old enough to *have* a settled reserve, but some
    # notices stay empty forever (reserve never published). Those pass the age-gate
    # and so are re-fetched ("not_settled") every 6h sweep, perpetually burning the
    # rate limit. The backfill stamps ``HistoricalData.reserve_detail_checked_at``
    # on a successful-but-empty fetch; the collector then skips deferring any notice
    # checked within this many hours, so a permanently-empty notice is re-checked at
    # most once per window (e.g. 48h => 0.5/day vs every 6h => 4/day, ~8x fewer
    # calls). The age-gate (defer only after the notice could have settled) and this
    # recheck-gate (back off after it was checked and found empty) are complementary.
    # 0 disables the recheck-gate.
    KONEPS_SCSBID_RESERVE_DETAIL_RECHECK_HOURS: int = 48
    # 개찰 1위(잠정) 수집 패스 — 실투찰 등록 공고 한정으로 개찰 직후 1위 업체 정보 +
    # 참가자 수를 날짜 윈도 조회로 채운다(ScsbidInfoService getOpengResultListInfo*).
    # 6h notify_award_results 태스크 경로에서 예외 격리로 호출된다.
    # 사이클당 처리 상한(실투찰이 소수라 작게 유지; 외부 rate limit 존중).
    KONEPS_OPENING_RESULT_COLLECTION_MAX_ITEMS: int = 20
    # 날짜 윈도 조회 페이지 크기(numOfRows, <=999). 개찰 하루치가 다수라 크게.
    KONEPS_OPENING_RESULT_PAGE_SIZE: int = 999
    # 미매칭(개찰 미공개/윈도 밖) 후보 재조회 backoff(시간). 매칭 실패도
    # opening_checked_at 을 스탬프해 이 시간 안에는 재조회하지 않는다. 0 disables.
    KONEPS_OPENING_RESULT_RECHECK_HOURS: int = 6
    # 개찰결과 목록 외부 호출 사이 딜레이(초). getOpengResultListInfo* 계열은 #146
    # 실측에서 같은 계열(reserve-detail)이 목록 계열(0.2s)보다 강한 rate limit(HTTP
    # 429)을 보여, 목록 페이지 딜레이보다 슬랙한 전용 1.0s 가 필요했다. 페이지 간 +
    # 윈도 그룹 간 모든 연속 호출 사이에 적용(첫 호출 전엔 없음). 0 disables.
    KONEPS_OPENING_RESULT_REQUEST_DELAY_SECONDS: float = 1.0
    # 윈도 그룹 fetch 가 연속 N회 실패하면 남은 그룹을 버리고 패스를 중단한다(#202
    # 쿼터-abort 패턴). 미스탬프로 남겨 다음 사이클에 재시도. 성공 시 카운터 리셋.
    KONEPS_OPENING_RESULT_MAX_CONSECUTIVE_ERRORS: int = 3
    # 날짜 윈도 페이지네이션 러너웨이 가드(페이지 상한). totalCount 결손 시 short-page
    # 종료에 의존하므로 상한을 둬 무한 루프를 막는다(collection.py max_pages 패턴).
    KONEPS_OPENING_RESULT_MAX_PAGES: int = 5
    BUSINESS_TYPE_ENRICHMENT_SCHEDULE_ENABLED: bool = False
    BUSINESS_TYPE_ENRICHMENT_INTERVAL_MINUTES: int = 15
    BUSINESS_TYPE_ENRICHMENT_BATCH_LIMIT: int = 50
    # scsbid 개찰결과(award-result) 페이지(예: .../link/PNPE027_01/single/?bidPbancNo=...)
    # 에는 business_type 정보가 없다. business_type_code는 *수집 시점*에 scsbid 개찰
    # OpenAPI item에서만 채워지므로, 수집 때 못 받은 개찰결과 URL 프로젝트는 detail HTML
    # 페치로도 영원히 채울 수 없다(페이지에 값이 없음). 이런 프로젝트를 enrichment 후보에서
    # 제외해 3분 주기 헛된 HTTP 페치를 막는다. 마커는 source_url 내 부분 문자열로 매칭한다.
    BUSINESS_TYPE_ENRICHMENT_SKIP_AWARD_RESULT_URLS: bool = True
    BUSINESS_TYPE_ENRICHMENT_AWARD_RESULT_URL_MARKERS: list[str] = Field(
        default_factory=lambda: ["PNPE027"]
    )

    # Category reclassification — SBERT prototype-based assignment for rows stuck at 'general'/'other'
    CATEGORY_RECLASSIFY_SCHEDULE_ENABLED: bool = False
    CATEGORY_RECLASSIFY_INTERVAL_MINUTES: int = 30
    CATEGORY_RECLASSIFY_BATCH_LIMIT: int = 100

    # Stale task-run reconciler — periodic janitor that closes out non-terminal
    # (running/queued) operator_strategy_runs and crawl_jobs whose age exceeds the
    # task hard time limit plus a safety margin. This is the durable backstop for
    # the hard-kill / worker-restart / DB-down cases that the in-task
    # finalize-on-failure path cannot cover (no ``finally`` runs on SIGKILL), so a
    # crashed run can never strand a ``running`` row that trips the operations
    # dashboard ``task_stale_queue: critical`` KPI forever. Safe idempotent
    # janitor (only flips already-stale non-terminal rows to ``failed`` with a
    # ``[reconciled]`` marker); recommended ON. Default OFF to honor the
    # codebase's opt-in-via-.env schedule convention.
    STALE_TASK_RECONCILER_SCHEDULE_ENABLED: bool = False
    STALE_TASK_RECONCILER_INTERVAL_MINUTES: int = 10
    # Extra slack added on top of CELERY_TASK_TIME_LIMIT_SECONDS before a
    # non-terminal row is considered orphaned. Keeps a task that is legitimately
    # running right up to its hard limit (then redelivered/finalized) from being
    # reconciled out from under itself.
    STALE_TASK_RECONCILER_GRACE_SECONDS: int = 300
    STALE_TASK_RECONCILER_BATCH_LIMIT: int = 500

    # ML release governance
    ML_RELEASE_MANIFEST_DIR: str = "models/manifests"
    ML_RELEASE_MANIFEST_ARCHIVE_DIR: str = "models/manifests/archive"
    ML_RELEASE_MANIFEST_RETENTION_LIMIT: int = 20
    ML_RELEASE_MANIFEST_SIGNING_KEY: str = ""
    ML_RELEASE_MANIFEST_SIGNING_KEY_ID: str = "local"
    ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE: bool = False
    ML_RELEASE_OBJECT_STORAGE_URL: str = ""
    ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH: bool = False
    ML_RELEASE_REMOTE_TRIGGER_BASE_URL: str = "http://localhost:3000"
    ML_RELEASE_REMOTE_TRIGGER_TIMEOUT_SECONDS: float = 120.0
    ML_RELEASE_HTTP_READY_PER_REQUEST_CAP_SECONDS: float = 10.0
    ML_RELEASE_PREDICTOR_GATE_POLICY: str = "standard"
    ML_RELEASE_PREDICTOR_GATE_MIN_DATASET_QUALITY_STATUS: str = ""
    ML_RELEASE_PREDICTOR_GATE_REQUIRE_REPORT: bool = False
    ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT: int = 5
    ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE: float = 0.03
    ML_RELEASE_PREDICTOR_GATE_MAX_GUARDRAIL_RATE: float = 0.25
    ML_RELEASE_PREDICTOR_GATE_MAX_FALLBACK_RATE: float = 0.25
    GROUP_CALIBRATION_MIN_SAMPLES: int = 100

    # Bid-decision action gates (BidDecisionService.evaluate_opportunity).
    # These pin the two hard-coded action gates that can auto-promote/hold an
    # opportunity independently of the operator's tuned bid_now/review
    # thresholds. Defaults preserve the historical literals exactly.
    #   - capacity-hold: at max active bids, hold (skip) while priority < this.
    #   - force-bid: promote to bid_now when probability >= P AND matched >= M,
    #     regardless of the priority score.
    ALLOCATION_CAPACITY_HOLD_PRIORITY_THRESHOLD: float = 0.8
    ALLOCATION_FORCE_BID_PROBABILITY_THRESHOLD: float = 0.8
    ALLOCATION_FORCE_BID_MATCHED_THRESHOLD: float = 0.7

    # AI Features
    MODEL_CACHE_DIR: str = "./models"
    ENABLE_PRICE_PREDICTION: bool = True
    ENABLE_BID_RECOMMENDATION: bool = True
    ENABLE_DOCUMENT_ANALYSIS: bool = True
    ENABLE_SEMANTIC_CLASSIFICATION: bool = True
    PREDICTION_DEFAULT_MINIMUM_BID_RATE: float = 0.0
    PREDICTION_CATEGORY_MINIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "software": 0.87,
            "service": 0.87,
            "technical-service": 0.88,
            "goods": 0.84,
            "construction": 0.87,
        }
    )
    PREDICTION_DEFAULT_MAXIMUM_BID_RATE: float = 1.0
    PREDICTION_CATEGORY_MAXIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "software": 1.0,
            "service": 1.0,
            "technical-service": 1.0,
            "goods": 1.0,
            "construction": 0.93,
        }
    )
    # Extra buffer added above the effective 낙찰하한율 when clamping scenario
    # candidates. This keeps the "conservative" scenario near the legal floor
    # without letting rounding/reserve-price variance land below it.
    PREDICTION_FLOOR_SAFETY_MARGIN_RATE: float = 0.001
    # 공사(construction) era-correct 법정 낙찰하한 tier(#197)가 해석되는 공고에서 3종
    # 투찰 시나리오(공격/추천/안전) 타깃을 "guardrail 이 최종 해석한 floor + offset"으로
    # 앵커한다. offset 은 무변환(예정가-basis) tier floor 대비 실낙찰(win÷기초금액)
    # 초과분의 백분위수다 — 값·규칙을 코드 분기가 아니라 선언 데이터로 둔다(§4.5.1/§4.5.3).
    #
    # 근거(2026-07-17 측정, clean-basis 공사 정착행 n=2,051; basis='clean',
    # winning_amount>0, era floor 해석 가능): (win÷기초금액 − 무변환 era_floor) 분포
    #   p25=+0.0016(공격, 최저적격 경쟁·낙하 위험), p50=+0.0053(추천), p75=+0.0148(안전).
    # p5=−0.0065(<0: 사정률<1 효과·지방계약 혼입) → 공격의 낙하 위험이 실재하는 근거이며,
    # p95≈+0.10(수의/단독응찰 tail)이라 ceiling(0.93)은 내리지 않는다.
    # ⚠ 표본/가설 기반이며 개찰 데이터 누적 시 재캘리브레이션 대상(운영자 튜너블).
    PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS: dict[str, float] = Field(
        default_factory=lambda: {
            "aggressive": 0.0016,
            "recommended": 0.0053,
            "safe": 0.0148,
        }
    )
    BUSINESS_GROUP_CODE_PREFIXES: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "construction": ["04"],
            "service": ["06"],
            "goods": ["01", "02"],
        }
    )
    BUSINESS_TYPE_TITLE_RULES: list[dict[str, str]] = Field(
        # Ordered specific → generic. _apply_title_rules breaks on first match,
        # so put narrower patterns first to avoid the generic 공사/용역 fallback
        # swallowing them.
        default_factory=lambda: [
            # Specific construction sub-types
            {"pattern": r"건축\s*공사", "code": "0411", "label": "건축공사"},
            {"pattern": r"토목\s*공사", "code": "0412", "label": "토목공사"},
            {"pattern": r"전기\s*공사|전기설비", "code": "0413", "label": "전기공사"},
            # Specific service sub-types
            {"pattern": r"학술\s*연구\s*용역|연구\s*개발\s*용역|연구\s*용역", "code": "0621", "label": "학술연구용역"},
            {"pattern": r"기술\s*용역|기술지원", "code": "0622", "label": "기술용역"},
            {"pattern": r"일반\s*용역|위탁\s*용역|운영\s*용역|관리\s*용역|체험학습|숙박형", "code": "0611", "label": "일반용역"},
            # Goods / 물품
            {"pattern": r"물품\s*구매|장비\s*구매|기기\s*구매|구매\s*입찰", "code": "0211", "label": "물품"},
            # Generic fallbacks — placed LAST so specific rules above win first.
            # 공사 catches 증축공사 / 개선공사 / 신축공사 / 보수공사 / etc. mapped to construction default.
            {"pattern": r"공사", "code": "0411", "label": "건축공사"},
            # 용역 catches the long tail (감리용역, 처리용역, 발굴용역, etc.) as service default.
            {"pattern": r"용역", "code": "0611", "label": "일반용역"},
        ]
    )
    BUSINESS_TYPE_COVERAGE_GATE: float = 0.95
    BUSINESS_GROUP_CALIBRATION_ENABLED: bool = True
    PREDICTION_GROUP_MINIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "construction": 0.87,
            "service": 0.70,
            "goods": 0.84,
        }
    )
    PREDICTION_GROUP_MAXIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "construction": 0.93,
            "service": 1.00,
            "goods": 1.00,
        }
    )
    # Agency-keyed bid-rate band (Lever 1 calibration).
    #
    # Provenance: derived from a 2026 29-notice 한국수산자원공단 적격심사 결과표.
    # For these 적격심사 용역 the winner is the LOWEST eligible bid sitting at the
    # per-notice 낙찰하한 (floor); a bid BELOW the realized floor is disqualified (낙하).
    # The realized winning floors clustered at 88.001–88.053% of base (mean 88.016%,
    # std 0.012%p). A backtest sweep over the 29 notices showed:
    #   - target 88.00% → 29/29 낙하 (too low — clears no floor),
    #   - target 88.06% → 1/29 낙하 (a single regime-shift anomaly) and 28/29 eligible
    #     at +0.0–0.044%p above the floor (single-digit ranks vs human 17/29 낙하).
    # So the MINIMUM (recommendation target) is set to the UPPER edge of the observed
    # band (0.8806), not 88.00, while the MAXIMUM caps the band at 0.882. The category-
    # scoped sample (agency-diluted) plus the service heuristic otherwise biases
    # predict_price toward ~0.91, so we recenter through the EXISTING guardrail band
    # (the same mechanism construction uses for its 0.93 ceiling) keyed by the issuing
    # agency. Keys are NORMALIZED agency tokens (whitespace-stripped, lowercased — see
    # normalize_agency_name) and match by normalized substring, so regional bureaus like
    # "한국수산자원공단동해본부" / "한국수산자원공단남해본부" / "한국수산자원공단 제주본부"
    # all inherit the headquarters band. Values are calibration data and are tunable by
    # the settled-result feedback loop (PR C).
    #
    # NOTE (mechanism — the band does NOT depend on a high-negotiated trigger):
    # the agency MINIMUM (0.8806) and MAXIMUM (0.882) are applied in
    # _apply_prediction_guardrails AFTER the predictor runs. The ceiling clamps
    # WHATEVER rate the predictor produced back into band UNCONDITIONALLY (ceiling
    # wins), and the minimum raises any under-band scenario — regardless of the
    # predictor's procurement_rate_band. There is NO reliance on service_high_negotiated:
    # in resolve_procurement_rate_band the bare substring "조사" does NOT select that
    # band. Specific 조사 compounds (영향조사 / 해저지형조사 / 석면조사 …) resolve to
    # service_price_competitive, while a bare "효과조사" resolves to None. The band works
    # purely as a post-predictor clamp keyed by the issuing agency.
    #
    # WARNING: agency keys MUST be HQ-specific. _resolve_agency_bid_rate matches by
    # NORMALIZED substring, so a short key like "수산" would over-capture unrelated
    # agencies — always key on the full headquarters token.
    PREDICTION_AGENCY_MINIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "한국수산자원공단": 0.8806,
        }
    )
    PREDICTION_AGENCY_MAXIMUM_BID_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            "한국수산자원공단": 0.882,
        }
    )
    # E[사정률] = E[예정가격 / 기초금액]. base 정합화(P0).
    #
    # The agency bands above (0.8806~0.882) were calibrated as 낙찰가 / 예정가:
    # scsbid persistence stored 예정가(planned_price) into base_amount when the
    # reserve detail lacked a real 기초금액, so this agency's settled rows had
    # base_amt == planned_est and every derived rate was a 예정가-basis ratio. The
    # MINIMUM 0.8806 was picked to sit at "88.06% of 예정가" — the sweep sweet spot
    # (28/29 eligible, single-digit ranks) — and was INTENDED to be applied to 예정가.
    # But 투찰가 is computed as rate × 사업금액(기초금액, #162). 예정가 is only
    # ~99.3~99.8% of 기초금액 (실측 한국수산자원공단 99.782% / 99.260%), so multiplying
    # a 예정가-basis band directly by 기초금액 lands ~+0.5%p high on the 예정가 basis
    # → the recommendation drifts to the 81~90th 낙찰 percentile (postmortem 52위/25위).
    #
    # Alignment: 기초금액-기준 목표율 = 예정가-기준 밴드율 × E[사정률]. The conversion is a
    # pure declarative multiplier applied to the AGENCY band edges ONLY (see
    # guardrail_core.resolve_band_assessment_rate). The category/group/legal 낙찰하한
    # guardrail is UNTOUCHED — resolve_floor_bid_rate still returns max(category, agency),
    # so a converted (lowered) agency floor can never undercut the hard floor.
    #
    # Values are per-agency empirical means of 예정가/기초금액 (refined by the settled
    # -result feedback loop as real 기초금액 accrues). The 1.0 default is a no-op for
    # agencies without a calibrated 사정률. WARNING: this is a HYPOTHESIS-based fix
    # (no post-opening 낙찰가 measured yet); re-calibrate once 개찰 results confirm.
    #
    # WARNING (basis coupling — the band and its assessment rate MOVE TOGETHER):
    # the AGENCY_MINIMUM/MAXIMUM values above are 예정가-basis BECAUSE they were
    # calibrated on pre-fix scsbid rows (base_amt == planned_price fallback). The
    # scsbid fix in this same PR stops that fallback, so NEW settled rows are
    # 기초금액-basis. If the feedback loop (PR C) ever RE-DERIVES the agency band
    # from post-fix rows, the re-derived band is ALREADY 기초금액-basis and its
    # entry here MUST be reset to 1.0 — keeping the old E[사정률] would apply the
    # conversion TWICE and push the band ~0.5%p too LOW (the inverse bug).
    #
    # WARNING (coverage coupling): when adding a new agency to AGENCY_MINIMUM/
    # MAXIMUM above, decide its entry here in the same change — a missing key is a
    # 1.0 no-op, which silently reintroduces the +0.5%p bug for any agency whose
    # band was calibrated on 예정가-basis rows. Keep the three maps in sync.
    # The construction scenario anchor adds a FOURTH coupling: an agency listed
    # ONLY here (assessment ≠ 1.0, no MIN/MAX band) converts the guardrail floor,
    # and the anchor then stacks its UNCONVERTED-basis offsets
    # (PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS) on that converted floor —
    # a mixed-basis anchor (the band-priority gate only fires on MIN/MAX bands).
    # If you ever add an assessment-only agency, gate the anchor for it too.
    #
    # WARNING (basis provenance — feedback loop must filter to clean rows): any
    # RE-CALIBRATION of this band from settled rows MUST restrict to
    # ``HistoricalData.base_amount_basis == 'clean'`` (see models.py + backfill
    # scripts/backfill_base_amount_basis.py). 66% of stored base_amount values are
    # derived (예정가 역산/VAT), and re-deriving the band from them re-injects the
    # exact 예정가-basis pollution these notes warn about.
    PREDICTION_AGENCY_BAND_ASSESSMENT_RATES: dict[str, float] = Field(
        default_factory=lambda: {
            # mean(99.782%, 99.260%) ≈ 0.9952 from the two postmortem notices.
            "한국수산자원공단": 0.9952,
        }
    )
    PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE: float = 1.0
    # Weight applied to the reserve-price (복수예비가격) implied bid rate when
    # blending it into the competitive base rate. 0 disables the prior entirely.
    # The effective weight is further scaled down by reserve sample_count so that
    # thin reserve evidence nudges the base rate only slightly. The blend always
    # stays UPSTREAM of apply_procurement_rate_band / clamp / the final
    # category-floor guardrail, so it can never undercut the bidding floor.
    PREDICTION_RESERVE_PRIOR_WEIGHT: float = 0.2
    PREDICTION_RESERVE_PRIOR_FULL_CONFIDENCE_SAMPLES: int = 8
    # Distribution-tail adjustment for recent high-award-rate patterns. Keeps
    # the recommended/base bid from being dragged below the recent settled high
    # band when goods/service outcomes cluster around 95-100% or small
    # construction awards are capped by the configured construction ceiling.
    PREDICTION_HIGH_RATE_TAIL_ADJUSTMENT_ENABLED: bool = True
    PREDICTION_SMALL_BUDGET_HIGH_RATE_BUDGET_MAX: float = 50_000_000.0
    PREDICTION_SMALL_BUDGET_HIGH_RATE_TARGET: float = 0.93
    # Minimum candidate base rate a small construction award must already show
    # before the small-budget target lift applies (below it the base stays put).
    PREDICTION_SMALL_BUDGET_HIGH_RATE_MIN_RATE: float = 0.925
    PREDICTION_BID_PRICE_GRANULARITY: int = 10
    PREDICTION_BID_PRICE_GRANULARITY_MIN_BUDGET: float = 1_000_000.0
    PREDICTION_BID_PRICE_ROUNDING_MODE: str = "floor"
    PRICE_PREDICTION_PREFERRED_PREDICTOR: str = "historical"
    PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS: bool = False
    PRICE_PREDICTION_LSTM_MODEL_PATH: str = ""
    PRICE_PREDICTION_ENSEMBLE_MODEL_PATH: str = ""
    PRICE_PREDICTION_LSTM_MIN_SAMPLES: int = 24
    PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES: int = 32
    PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES: int = 5
    PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE: int = 5
    CLASSIFIER_EMBEDDING_MODEL: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY: bool = True
    CLASSIFIER_SEMANTIC_MATCH_THRESHOLD: float = 0.35
    # Semantic band edges expressed RELATIVE to CLASSIFIER_SEMANTIC_MATCH_THRESHOLD
    # (same tuning family). ``strong`` = threshold + margin (매우 높음),
    # ``base`` = threshold - margin (보통); the two absolute ceilings gate the
    # low-similarity penalties (매우 낮음 <= very_low_max, 낮음 <= low_max).
    CLASSIFIER_SEMANTIC_STRONG_MARGIN: float = 0.2
    CLASSIFIER_SEMANTIC_BASE_MARGIN: float = 0.12
    CLASSIFIER_SEMANTIC_VERY_LOW_MAX: float = 0.03
    CLASSIFIER_SEMANTIC_LOW_MAX: float = 0.1

    # KONEPS / Crawling
    KONEPS_BASE_URL: str = "https://www.g2b.go.kr"
    KONEPS_HOME_URL: str = "https://www.g2b.go.kr/"
    KONEPS_NOTICE_LIST_URL: str = (
        "https://www.g2b.go.kr/pt/menu/selectSubFrame.do?framesrc=/ep/tbid/tbidList.do"
    )
    KONEPS_OPENAPI_BID_PUBLIC_INFO_URL: str = (
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
    )
    KONEPS_OPENAPI_SCSBID_INFO_URL: str = (
        "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
    )
    KONEPS_OPENAPI_SERVICE_KEY: str = ""
    KONEPS_OPENAPI_ENCODED_SERVICE_KEY: str = ""
    KONEPS_OPENAPI_MAX_ITEMS: int = 500
    KONEPS_OPENAPI_COLLECTION_PAGE_SIZE: int = 100  # BidPublicInfoService 호출당 페이지 크기
    KONEPS_OPENAPI_TIMEOUT_SECONDS: int = 20
    KONEPS_OPENAPI_REQUEST_DELAY_SECONDS: float = 0.2  # inter-page throttle for BidPublicInfoService pagination
    # field_contract 순수 검증기를 라이브 수집 루프에 **관찰 전용**으로 배선하는 토글
    # (#227 검증기 프로덕션 소비자 0 갭 해소). ON 이어도 수집 동작은 불변 — 위반·미지 필드를
    # run 단위로 세어 요약 로그 1줄 + metadata 만 남기고 행을 드롭/실패시키지 않는다. soft 라
    # 기본 ON. OFF 면 관찰기를 아예 만들지 않아 무비용 통과한다.
    KONEPS_FIELD_CONTRACT_LIVE_CHECK: bool = True
    KONEPS_HEADLESS: bool = True
    KONEPS_TIMEOUT_MS: int = 30000

    # 국세청 사업자등록정보 검증(상태조회/진위확인) — 공공데이터포털(odcloud)
    # 키가 비어 있으면 외부 호출을 절대 하지 않고 graceful 하게 status='unknown' 을
    # 반환한다(운영자가 SMTP 처럼 나중에 키를 붙이는 opt-in 경로). 시크릿(서비스키)은
    # 로그/응답/텔레메트리에 남기지 않는다(§8).
    NTS_BUSINESS_VERIFICATION_SERVICE_KEY: str = ""
    NTS_BUSINESS_VERIFICATION_BASE_URL: str = (
        "https://api.odcloud.kr/api/nts-businessman/v1"
    )
    NTS_BUSINESS_VERIFICATION_TIMEOUT_SECONDS: int = 10
    # 사업자번호 해시용 pepper(HMAC-SHA256 key). 원문 번호는 저장하지 않고 이 pepper 로
    # 계산한 HMAC 해시만 저장한다. 빈 값도 유효한(길이 0) HMAC 키라 결정적이며 중복
    # 조회에 문제 없다. 다만 유효 사업자번호 공간이 작아(~10^8) pepper 가 비면 무차별
    # 대입 저항이 약하다 — 운영에서는 실제 pepper 설정을 권장한다(문서화된 known-limitation,
    # 다른 시크릿과 결합하지 않는 단일 출처 값).
    BUSINESS_NUMBER_HASH_PEPPER: str = ""
    KONEPS_USER_AGENT: str = "bid-vector-bot/0.1"
    KONEPS_REQUEST_DELAY_MS: int = 750
    KONEPS_SEARCH_WAIT_MS: int = 4000
    KONEPS_MAX_ITEMS: int = 10
    KONEPS_RETRY_COUNT: int = 2
    KONEPS_RETRY_BACKOFF_MS: int = 1500

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org"
    TELEGRAM_SEND_TIMEOUT_SECONDS: int = 10
    TELEGRAM_DECISION_PRIORITY_THRESHOLD: float = 0.78
    TELEGRAM_DECISION_PROBABILITY_THRESHOLD: float = 0.8
    TELEGRAM_WEBHOOK_SECRET: str = ""
    TELEGRAM_POLLING_LIMIT: int = 20
    TELEGRAM_POLLING_TIMEOUT_SECONDS: int = 0
    TELEGRAM_POLLING_SCHEDULE_ENABLED: bool = False
    TELEGRAM_POLLING_INTERVAL_SECONDS: int = 30

    # Email delivery (투찰 보고서 메일 전달)
    # 안전 기본값 = DRY-RUN: 렌더링/로깅만 하고 SMTP 연결·외부 송신을 절대 하지 않는다.
    # 라이브 송신은 향후 opt-in — EMAIL_DELIVERY_ENABLED=True + EMAIL_DRY_RUN=False 로
    # 명시적으로 켜야만 smtplib 경로가 실행된다(기본은 OFF, 이 저장소에서 미검증).
    EMAIL_DELIVERY_ENABLED: bool = False
    EMAIL_DRY_RUN: bool = True
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    # 시크릿 — 절대 로그/응답/텔레메트리에 남기지 않는다.
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    # STARTTLS(587) — 평문 연결 후 TLS 승격. 암시적 SSL(465, SMTP_USE_SSL)과 구분.
    SMTP_USE_TLS: bool = True
    # True → 암시적 SSL(465, smtplib.SMTP_SSL). SMTP_USE_TLS(STARTTLS/587)와 상호배타 — SSL이 우선.
    SMTP_USE_SSL: bool = False
    EMAIL_SEND_TIMEOUT_SECONDS: int = 15

    # Opportunity analysis — strength / risk cutoffs
    # Thresholds that gate the operator-facing 강점(strength) and 리스크(risk)
    # phrases in OpportunityAnalysisService._build_strengths / _build_risk_flags.
    # Names encode the comparison direction: *_MIN is a floor (value >= / >, or a
    # "below this is a risk" boundary) and *_MAX is a ceiling ("at or above/below
    # this is a risk"). Defaults are the current in-code literals — changing them
    # only shifts when the advisory phrase renders; it does not alter any score.
    OPPORTUNITY_STRENGTH_COMPETITIVENESS_MIN: float = 0.75
    OPPORTUNITY_STRENGTH_MARGIN_MIN: float = 0.72
    OPPORTUNITY_STRENGTH_PRICE_CONFIDENCE_MIN: float = 0.75
    OPPORTUNITY_STRENGTH_PROBABILITY_MIN: float = 0.75
    OPPORTUNITY_RISK_SIMILARITY_MIN: float = 0.4  # avg similarity below -> risk
    OPPORTUNITY_RISK_PRICE_CONFIDENCE_MIN: float = 0.75  # confidence below -> risk
    OPPORTUNITY_RISK_MARGIN_MAX: float = 0.45  # margin at/below -> risk
    OPPORTUNITY_RISK_COMPLEXITY_MAX: float = 0.72  # complexity at/above -> risk

    # License-eligibility gate — screen recommendation candidates against the
    # operator's held licenses (CompanyProfile.license_codes) vs each notice's
    # published 면허요건 (Project.eligibility_raw["license_limits"]) via
    # app.services.license_eligibility.assess_license_eligibility. When ON the
    # candidate screening in StrategyMonitoringService drops only notices whose
    # verdict is DATA-CONFIRMED ``ineligible`` (we lack a required license),
    # BEFORE the expensive per-candidate ML analysis. ``unknown`` (no 면허요건
    # data or no held-license data — 92% of open notices) and ``eligible`` are
    # NEVER suppressed, so the coverage gap can only lose precision, never recall.
    # Default OFF: while disabled the gate code path never runs (no extra DB
    # query, no behavior change), so merging this is a no-op for live
    # recommendations. Activate via .env only after reviewing the read-only
    # scripts/report_license_gate_impact.py simulation.
    LICENSE_ELIGIBILITY_GATE_ENABLED: bool = False

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8080",
    ]

    @property
    def uses_in_memory_celery(self) -> bool:
        """Return whether Celery should execute eagerly against the in-memory transport."""
        return (self.CELERY_BROKER_URL or "").strip().lower().startswith("memory://")

    @property
    def g2_evidence_target_operator_ids(self) -> List[int]:
        """Parse the comma-separated G-2 evidence target operator ids into ints.

        Deduplicates while preserving order and drops blank/non-positive tokens,
        matching the CSV->list pattern used elsewhere (e.g. KONEPS categories).
        """
        ids: List[int] = []
        seen: set[int] = set()
        for token in str(self.G2_EVIDENCE_TARGET_OPERATOR_IDS or "").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = int(token)
            except ValueError:
                continue
            if value > 0 and value not in seen:
                ids.append(value)
                seen.add(value)
        return ids

    @model_validator(mode="after")
    def _compose_database_url(self) -> "Settings":
        """Allow split DATABASE_* env vars to compose DATABASE_URL deterministically."""
        if all(
            [
                self.DATABASE_USER,
                self.DATABASE_PASSWORD is not None,
                self.DATABASE_HOST,
                self.DATABASE_PORT,
                self.DATABASE_NAME,
            ]
        ):
            encoded_password = quote_plus(self.DATABASE_PASSWORD or "")
            self.DATABASE_URL = (
                f"postgresql+psycopg://{self.DATABASE_USER}:{encoded_password}"
                f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
            )
        elif self.DATABASE_URL and self.DATABASE_URL != DEFAULT_DATABASE_URL:
            pass

        if (not self.uses_in_memory_celery) and (
            not self.CELERY_RESULT_BACKEND
            or self.CELERY_RESULT_BACKEND == DEFAULT_CELERY_RESULT_BACKEND
        ):
            self.CELERY_RESULT_BACKEND = _to_celery_database_result_backend(
                self.DATABASE_URL
            )
        elif self.uses_in_memory_celery and not self.CELERY_RESULT_BACKEND:
            self.CELERY_RESULT_BACKEND = DEFAULT_CELERY_RESULT_BACKEND

        return self


settings = Settings()
