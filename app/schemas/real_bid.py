"""실투찰(real-bid) 트랙 입출력 스키마.

운영자가 KONEPS 에 실제로 제출한 투찰가를 등록/조회하는 최소 엔드포인트의
Pydantic 경계. 실투찰가는 시스템 추천(``recommended_amount``)과 별도로
``submitted_bid_amount`` 에 저장된다(§2 정직 명세: 추천과 실제 제출은 구분).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RealBidRecordRequest(BaseModel):
    """실투찰 등록 요청. project_id 또는 notice_number 중 하나 이상 필수."""

    project_id: Optional[int] = Field(default=None, ge=1, description="대상 프로젝트 ID")
    notice_number: Optional[str] = Field(
        default=None,
        max_length=100,
        description="공고번호(차수 접미사 -000 은 매칭 시 제거). project_id 없을 때 사용.",
    )
    bid_amount: float = Field(gt=0, description="실제 제출한 투찰가(원)")
    floor_rate: Optional[float] = Field(
        default=None,
        gt=0,
        le=1.5,
        description="공고 낙찰하한율(비율, 예: 0.87745). 개찰 후 적격 판정에 사용.",
    )
    submitted_at: Optional[datetime] = Field(
        default=None, description="실제 제출 시각(생략 시 등록 시각)"
    )
    note: Optional[str] = Field(
        default=None, max_length=500, description="운영자 메모(감사 근거에 추가)"
    )

    @model_validator(mode="after")
    def _require_project_or_notice(self) -> "RealBidRecordRequest":
        if self.project_id is None and not (self.notice_number or "").strip():
            raise ValueError("project_id 또는 notice_number 중 하나는 필수입니다.")
        return self


class RealBidRecord(BaseModel):
    """실투찰 기록 1건(개찰 결과 상태 포함)."""

    model_config = ConfigDict(from_attributes=True)

    decision_record_id: int
    project_id: Optional[int]
    notice_number: Optional[str]
    project_title: Optional[str]
    submitted_bid_amount: Optional[float]
    submitted_floor_rate: Optional[float]
    recommended_amount: Optional[float]
    submitted_at: Optional[datetime]
    award_notified_at: Optional[datetime]
    decision_status: str
    winning_company: Optional[str]
    winning_amount: Optional[float]
    award_public: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class RealBidListResponse(BaseModel):
    """실투찰 목록 응답(최근 순, 운영자 스코프)."""

    operator_id: int
    count: int
    records: List[RealBidRecord]
