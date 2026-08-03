"""Configuration fields owned by durable inference delivery."""

from pydantic_settings import BaseSettings


class InferenceOutboxSettings(BaseSettings):
    INFERENCE_OUTBOX_SCHEDULE_ENABLED: bool = True
    INFERENCE_OUTBOX_INTERVAL_SECONDS: int = 30
    INFERENCE_OUTBOX_BATCH_LIMIT: int = 50
    INFERENCE_OUTBOX_LOCK_TIMEOUT_SECONDS: int = 600
    INFERENCE_OUTBOX_MAX_ATTEMPTS: int = 5
    INFERENCE_OUTBOX_RETRY_BASE_SECONDS: int = 5
