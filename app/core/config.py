"""Application configuration"""
from typing import List
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    DATABASE_URL: str = "postgresql+psycopg://postgres:password@localhost:5432/bid_vector_db"
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
    CELERY_BROKER_URL: str = "memory://"
    CELERY_RESULT_BACKEND: str = "cache+memory://"

    # AI Features
    MODEL_CACHE_DIR: str = "./models"
    ENABLE_PRICE_PREDICTION: bool = True
    ENABLE_BID_RECOMMENDATION: bool = True
    ENABLE_DOCUMENT_ANALYSIS: bool = True
    ENABLE_SEMANTIC_CLASSIFICATION: bool = True
    CLASSIFIER_EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY: bool = True
    CLASSIFIER_SEMANTIC_MATCH_THRESHOLD: float = 0.35

    # KONEPS / Crawling
    KONEPS_BASE_URL: str = "https://www.g2b.go.kr"
    KONEPS_HOME_URL: str = "https://www.g2b.go.kr/"
    KONEPS_NOTICE_LIST_URL: str = "https://www.g2b.go.kr/pt/menu/selectSubFrame.do?framesrc=/ep/tbid/tbidList.do"
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

    @model_validator(mode="after")
    def _compose_database_url(self) -> "Settings":
        """Allow split DATABASE_* env vars to override the default DATABASE_URL."""
        default_database_url = "postgresql+psycopg://postgres:password@localhost:5432/bid_vector_db"
        if self.DATABASE_URL and self.DATABASE_URL != default_database_url:
            return self
        if all([
            self.DATABASE_USER,
            self.DATABASE_PASSWORD is not None,
            self.DATABASE_HOST,
            self.DATABASE_PORT,
            self.DATABASE_NAME,
        ]):
            encoded_password = quote_plus(self.DATABASE_PASSWORD or "")
            self.DATABASE_URL = (
                f"postgresql+psycopg://{self.DATABASE_USER}:{encoded_password}"
                f"@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
            )
        return self


settings = Settings()
