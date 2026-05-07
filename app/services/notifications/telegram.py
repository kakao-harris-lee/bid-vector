"""Telegram notification service skeleton."""
from app.core.config import settings


class TelegramNotificationService:
    """Send messages to Telegram when configured."""

    def is_configured(self) -> bool:
        """Return whether Telegram settings are available."""
        return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)

    def build_message(self, title: str, message: str, url: str | None = None) -> str:
        """Build a consistent Telegram notification message."""
        parts = [f"[ {title} ]", message]
        if url:
            parts.append(f"상세보기: {url}")
        return "\n".join(parts)
