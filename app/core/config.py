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
    API_PORT: int = 8000
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

    # ML release governance
    ML_RELEASE_MANIFEST_DIR: str = "models/manifests"
    ML_RELEASE_MANIFEST_ARCHIVE_DIR: str = "models/manifests/archive"
    ML_RELEASE_MANIFEST_RETENTION_LIMIT: int = 20
    ML_RELEASE_MANIFEST_SIGNING_KEY: str = ""
    ML_RELEASE_MANIFEST_SIGNING_KEY_ID: str = "local"
    ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE: bool = False
    ML_RELEASE_OBJECT_STORAGE_URL: str = ""
    ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH: bool = False
    ML_RELEASE_PREDICTOR_GATE_POLICY: str = "standard"
    ML_RELEASE_PREDICTOR_GATE_MIN_DATASET_QUALITY_STATUS: str = ""
    ML_RELEASE_PREDICTOR_GATE_REQUIRE_REPORT: bool = False
    ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT: int = 5
    ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE: float = 0.03
    ML_RELEASE_PREDICTOR_GATE_MAX_GUARDRAIL_RATE: float = 0.25
    ML_RELEASE_PREDICTOR_GATE_MAX_FALLBACK_RATE: float = 0.25

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

    # KONEPS / Crawling
    KONEPS_BASE_URL: str = "https://www.g2b.go.kr"
    KONEPS_HOME_URL: str = "https://www.g2b.go.kr/"
    KONEPS_NOTICE_LIST_URL: str = (
        "https://www.g2b.go.kr/pt/menu/selectSubFrame.do?framesrc=/ep/tbid/tbidList.do"
    )
    KONEPS_OPENAPI_BID_PUBLIC_INFO_URL: str = (
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
    )
    KONEPS_OPENAPI_SERVICE_KEY: str = ""
    KONEPS_OPENAPI_ENCODED_SERVICE_KEY: str = ""
    KONEPS_OPENAPI_MAX_ITEMS: int = 100
    KONEPS_OPENAPI_TIMEOUT_SECONDS: int = 20
    KONEPS_HEADLESS: bool = True
    KONEPS_TIMEOUT_MS: int = 30000
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
