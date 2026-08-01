"""Telegram notification, callback and status schemas."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from app.core.constants import DecisionStatus, PaperBidAction


class TelegramNotificationRequest(BaseModel):
    title: str
    message: str
    url: Optional[str] = None


class TelegramCallbackChat(BaseModel):
    id: int


class TelegramCallbackMessage(BaseModel):
    message_id: int
    chat: TelegramCallbackChat


class TelegramCallbackQuery(BaseModel):
    id: str
    data: str
    message: Optional[TelegramCallbackMessage] = None


class TelegramCallbackUpdateRequest(BaseModel):
    update_id: Optional[int] = None
    callback_query: TelegramCallbackQuery


class TelegramActionResponse(BaseModel):
    status: str
    detail: str
    decision_record_id: Optional[int] = None
    action: Optional[PaperBidAction] = None
    decision_status: Optional[DecisionStatus] = None


class TelegramSyncResponse(BaseModel):
    status: str
    detail: str
    processed_count: int
    processed_update_ids: List[int] = Field(default_factory=list)
    known_chat_ids: List[str] = Field(default_factory=list)


class TelegramStatusResponse(BaseModel):
    configured: bool
    status: Literal["healthy", "watch", "error"] = "healthy"
    detail: str = ""
    delivery_chat_id: Optional[str] = None
    pending_update_count: int = 0
    webhook_url: str = ""
    has_custom_certificate: bool = False
    known_chat_ids: List[str] = Field(default_factory=list)
