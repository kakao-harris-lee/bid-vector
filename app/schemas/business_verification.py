"""사업자번호 검증(상태조회/진위확인) 입출력 스키마.

``POST /api/v1/operator/business-verification`` 의 Pydantic 경계(외부 입력은 반드시
스키마 통과, §4). 요청의 ``business_number`` 는 **transient** 다 — 외부 호출과 해시
계산에만 쓰고 원문을 저장·응답하지 않는다. 응답은 masked 번호/상태/시각/고지만 담고
원문 번호·서비스키를 절대 포함하지 않는다(설계 §비기능, §8).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.verification.hashing import is_valid_business_number

_DIGITS = re.compile(r"\D+")


class BusinessVerificationRequest(BaseModel):
    """검증 요청. ``start_date`` + ``representative_name`` 이 모두 있으면 진위확인,
    아니면 상태조회로 처리된다(서비스가 분기)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    business_number: str = Field(
        description="사업자등록번호(하이픈 허용). 숫자 10자리만 유효 — 원문은 저장/응답되지 않음",
    )
    start_date: Optional[str] = Field(
        default=None,
        description="개업일자(진위확인용, 선택). YYYYMMDD 또는 YYYY-MM-DD → 8자리로 정규화",
    )
    representative_name: Optional[str] = Field(
        default=None,
        description="대표자명(진위확인용, 선택). start_date 와 함께 있어야 진위확인 수행",
    )

    @field_validator("business_number")
    @classmethod
    def _validate_business_number(cls, value: str) -> str:
        if not is_valid_business_number(value):
            raise ValueError("사업자등록번호는 숫자 10자리여야 합니다.")
        return value

    @field_validator("start_date")
    @classmethod
    def _normalize_start_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        digits = _DIGITS.sub("", value)
        if len(digits) != 8:
            raise ValueError("개업일자는 YYYYMMDD(8자리) 형식이어야 합니다.")
        return digits

    @field_validator("representative_name")
    @classmethod
    def _empty_name_to_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        return value


class BusinessVerificationResponse(BaseModel):
    """검증 응답 — 원문 번호/서비스키 없이 masked 번호와 상태만 노출한다."""

    status: str = Field(
        description="검증 상태(unverified/verified/inactive/mismatch/unknown)",
    )
    masked_number: str = Field(
        description="마스킹된 사업자번호(예: 123-45-*****). 원문은 반환되지 않음",
    )
    verified_at: Optional[datetime] = Field(
        default=None,
        description="외부 조회가 실제로 수행된 시각(키 미구성/미조회면 null)",
    )
    detail: Optional[str] = Field(
        default=None,
        description="운영자용 정직 고지(예: unknown 사유 = '검증 미구성')",
    )
    current_operator_id: int = Field(description="반영이 스코프된 운영자 id")
    current_operator_username: str = Field(description="반영이 스코프된 운영자 username")
