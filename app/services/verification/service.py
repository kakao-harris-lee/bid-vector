"""사업자번호 검증 오케스트레이션 — 포트 호출 + 프로필 영속화(operator-scoped).

라우터는 얇게 유지하고(§4) 이 서비스가 (1) 포트 선택(진위확인 vs 상태조회), (2) 원문
번호로 pepper HMAC 해시 계산, (3) 해석된 operator 자신의 ``CompanyProfile`` 에만 해시/
상태/masked payload/검증시각 반영을 담당한다. **원문 사업자번호는 이 함수의 인메모리
지역변수로만 존재하고 저장·로그·응답 어디에도 남지 않는다**(설계 §비기능, §8).

키가 없으면 팩토리가 Unavailable 어댑터를 돌려주므로 외부 호출 없이 status='unknown'
으로 graceful 하게 끝난다(예외 아님).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import ensure_operator_profile_for
from app.core.time import utc_now
from app.models.models import User
from app.services.verification.hashing import (
    hash_business_number,
    mask_business_number,
)
from app.services.verification.nts_adapter import build_business_verification_port
from app.services.verification.ports import BusinessVerificationPort, VerificationResult
from app.services.verification.status import BusinessVerificationStatus

# 상태 → 운영자용 정직 고지(선언 룩업, §4.5.1). unknown 은 사유별로 세분한다.
_DETAIL_BY_STATUS: dict[str, str] = {
    BusinessVerificationStatus.VERIFIED.value: "사업자 상태가 확인되었습니다(계속사업자/진위 일치).",
    BusinessVerificationStatus.INACTIVE.value: "휴업 또는 폐업 상태입니다. 추천 진행 전 확인이 필요합니다.",
    BusinessVerificationStatus.MISMATCH.value: "진위확인 결과 불일치입니다. 개업일자·대표자명을 확인해 주세요.",
    BusinessVerificationStatus.UNVERIFIED.value: "아직 검증되지 않았습니다.",
}
_DETAIL_NO_KEY = "검증 미구성 — data.go.kr 서비스 키가 설정되지 않아 외부 조회를 건너뛰었습니다."
_DETAIL_UNKNOWN_ERROR = "외부 조회에 실패했거나 조회되지 않았습니다(확인 필요)."


@dataclass(frozen=True)
class VerificationOutcome:
    """서비스 반환 DTO — 원문 번호/서비스키 없이 상태·masked 번호·시각·고지만 담는다."""

    status: str
    masked_number: str
    verified_at: Optional[datetime]
    detail: str


def _detail_for(result: VerificationResult) -> str:
    if result.status == BusinessVerificationStatus.UNKNOWN.value:
        reason = (result.masked_payload or {}).get("reason")
        if reason == "verification_unavailable":
            return _DETAIL_NO_KEY
        return _DETAIL_UNKNOWN_ERROR
    return _DETAIL_BY_STATUS.get(result.status, _DETAIL_UNKNOWN_ERROR)


def _run_check(
    port: BusinessVerificationPort,
    *,
    business_number: str,
    start_date: Optional[str],
    representative_name: Optional[str],
) -> VerificationResult:
    """진위확인에 필요한 값(개업일+대표자명)이 모두 있으면 진위확인, 아니면 상태조회."""
    if start_date and representative_name:
        return port.check_authenticity(
            business_number,
            start_date=start_date,
            representative_name=representative_name,
        )
    return port.check_status(business_number)


def verify_business_number(
    db: Session,
    *,
    operator: User,
    business_number: str,
    start_date: Optional[str] = None,
    representative_name: Optional[str] = None,
    port: Optional[BusinessVerificationPort] = None,
    pepper: Optional[str] = None,
) -> VerificationOutcome:
    """사업자번호를 검증하고 결과를 operator 자신의 프로필에 반영한다(원문 미저장).

    ``port`` 미주입 시 설정 기반 팩토리로 결정한다 — 키가 없으면 Unavailable 어댑터라
    외부 호출 없이 unknown 으로 끝난다. ``pepper`` 미주입 시 설정값을 쓴다. 반영은
    해석된 ``operator`` 의 ``CompanyProfile`` 한 행에만 한정된다(per-operator 스코프).
    """
    resolved_port = port or build_business_verification_port()
    resolved_pepper = settings.BUSINESS_NUMBER_HASH_PEPPER if pepper is None else pepper

    result = _run_check(
        resolved_port,
        business_number=business_number,
        start_date=start_date,
        representative_name=representative_name,
    )

    profile = ensure_operator_profile_for(db, operator)
    # 원문 번호는 여기서 해시/마스킹으로만 소비하고 저장하지 않는다.
    profile.business_registration_number_hash = hash_business_number(
        business_number, resolved_pepper
    )
    profile.business_verification_status = result.status
    profile.business_verification_payload = json.dumps(
        result.masked_payload or {}, ensure_ascii=False
    )
    verified_at = utc_now() if result.checked else None
    profile.business_verified_at = verified_at
    db.commit()
    db.refresh(profile)

    return VerificationOutcome(
        status=result.status,
        masked_number=mask_business_number(business_number),
        verified_at=verified_at,
        detail=_detail_for(result),
    )
