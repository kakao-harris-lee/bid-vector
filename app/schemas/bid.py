"""Legacy bid CRUD schemas."""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


class BidBase(BaseModel):
    bid_amount: float
    proposed_timeline: int


class BidCreate(BidBase):
    project_id: int
    description: str


class BidUpdate(BaseModel):
    bid_amount: Optional[float] = None
    description: Optional[str] = None


class BidResponse(BidBase):
    id: int
    project_id: int
    operator_id: int
    user_id: int
    status: str
    decision_record_id: Optional[int] = None
    decision_status: Optional[
        Literal["planned", "reviewing", "submitted", "skipped"]
    ] = None
    score: Optional[float] = None
    created_at: datetime
