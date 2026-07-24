"""사업자번호 검증 상태 — 허용값 단일 출처 + 외부코드 매핑표(선언 데이터, §4.5).

``CompanyProfile.business_verification_status`` 컬럼과 응답 스키마가 공유하는 허용값의
단일 출처다(매직값 금지 §4.5.1). 모델 컬럼 자체는 plain ``String`` 이고(모델↔서비스
순환 회피, onboarding ``DecisionStatus`` 패턴 미러) 이 enum 이 의미를 정의한다.

국세청 API 코드 → 내부 상태 매핑은 코드 분기(중첩 if)가 아니라 **룩업표**로 둔다
(§4.5.2). 새 코드가 생기면 표에 한 줄만 추가한다.
"""

from __future__ import annotations

import enum


class BusinessVerificationStatus(str, enum.Enum):
    """운영자 프로필의 사업자번호 검증 상태(설계 §데이터모델).

    * ``UNVERIFIED`` — 검증 시도 전 기본값.
    * ``VERIFIED``   — 계속사업자(상태조회) 또는 진위 일치(진위확인).
    * ``INACTIVE``   — 휴업자/폐업자(추천 진행 전 warning 대상).
    * ``MISMATCH``   — 진위확인 불일치(대표자명/개업일 대조 실패).
    * ``UNKNOWN``    — 미조회/외부실패/키 미구성(차단이 아니라 "확인 필요").
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    INACTIVE = "inactive"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


# 상태조회(status) — b_stt_cd(사업자상태코드) → 내부 상태(선언 룩업, §4.5.2).
#   01=계속사업자, 02=휴업자, 03=폐업자.
STATUS_CODE_MAP: dict[str, BusinessVerificationStatus] = {
    "01": BusinessVerificationStatus.VERIFIED,
    "02": BusinessVerificationStatus.INACTIVE,
    "03": BusinessVerificationStatus.INACTIVE,
}

# 상태조회 — b_stt(상태 텍스트) 폴백 매핑(코드가 비어 온 응답 대비).
STATUS_TEXT_MAP: dict[str, BusinessVerificationStatus] = {
    "계속사업자": BusinessVerificationStatus.VERIFIED,
    "휴업자": BusinessVerificationStatus.INACTIVE,
    "폐업자": BusinessVerificationStatus.INACTIVE,
}

# 진위확인(validate) — valid 코드 → 내부 상태(선언 룩업).
#   01=일치, 02=불일치.
VALIDATE_CODE_MAP: dict[str, BusinessVerificationStatus] = {
    "01": BusinessVerificationStatus.VERIFIED,
    "02": BusinessVerificationStatus.MISMATCH,
}


def map_status_code(b_stt_cd: str | None, b_stt: str | None) -> BusinessVerificationStatus:
    """상태조회 응답을 내부 상태로 매핑한다(코드 우선, 텍스트 폴백, 미지정→unknown)."""
    code = (b_stt_cd or "").strip()
    if code in STATUS_CODE_MAP:
        return STATUS_CODE_MAP[code]
    text = (b_stt or "").strip()
    if text in STATUS_TEXT_MAP:
        return STATUS_TEXT_MAP[text]
    return BusinessVerificationStatus.UNKNOWN


def map_validate_code(valid: str | None) -> BusinessVerificationStatus:
    """진위확인 응답을 내부 상태로 매핑한다(미지정→unknown)."""
    code = (valid or "").strip()
    return VALIDATE_CODE_MAP.get(code, BusinessVerificationStatus.UNKNOWN)
