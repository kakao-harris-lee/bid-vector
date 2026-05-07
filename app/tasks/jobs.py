"""Background job skeletons."""
from app.tasks.celery_app import celery_app


@celery_app.task(name="jobs.collect_koneps_notices")
def collect_koneps_notices() -> dict:
    """Placeholder job for KONEPS crawling."""
    return {"status": "queued", "detail": "KONEPS crawler skeleton is ready."}


@celery_app.task(name="jobs.send_telegram_notification")
def send_telegram_notification() -> dict:
    """Placeholder job for Telegram notifications."""
    return {"status": "queued", "detail": "Telegram notification skeleton is ready."}
