"""Application configuration"""
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""

    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = True
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "mysql+pymysql://root:password@localhost:3306/bid_vector_db"

    # JWT
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # AI Features
    MODEL_CACHE_DIR: str = "./models"
    ENABLE_PRICE_PREDICTION: bool = True
    ENABLE_BID_RECOMMENDATION: bool = True
    ENABLE_DOCUMENT_ANALYSIS: bool = True

    # KONEPS / Crawling
    KONEPS_BASE_URL: str = "https://www.g2b.go.kr"
    KONEPS_HEADLESS: bool = True
    KONEPS_TIMEOUT_MS: int = 30000
    KONEPS_USER_AGENT: str = "bid-vector-bot/0.1"

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8080",
    ]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
