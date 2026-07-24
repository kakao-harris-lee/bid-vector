"""사업자번호 검증(국세청 상태조회/진위확인) 서비스 패키지.

원문 사업자번호는 저장·로그·응답 어디에도 남기지 않는다(설계 §비기능, CLAUDE.md §8) —
pepper HMAC-SHA256 해시만 저장하고 표시는 masked 번호만 쓴다. 외부 호출은 서비스키가
구성됐을 때만 일어나며(팩토리 게이팅), 키가 없으면 status='unknown' 으로 graceful 하게
끝난다(HTTP 호출 없음).

구조(§4.7):
* :mod:`status`     — 허용 상태 enum + 외부코드→상태 룩업표(선언 데이터).
* :mod:`hashing`    — normalize/hash/mask 순수 함수(I/O 없음).
* :mod:`ports`      — ``BusinessVerificationPort`` ABC + ``VerificationResult`` DTO(계약).
* :mod:`nts_adapter`— NTS/Unavailable 어댑터 + 설정 기반 팩토리(구현 선택).
* :mod:`service`    — 포트 호출 + operator 프로필 영속화 오케스트레이션.
"""

from app.services.verification.hashing import (
    hash_business_number,
    is_valid_business_number,
    mask_business_number,
    normalize_business_number,
)
from app.services.verification.nts_adapter import (
    NtsBusinessVerificationAdapter,
    UnavailableBusinessVerificationAdapter,
    build_business_verification_port,
)
from app.services.verification.ports import (
    BusinessVerificationPort,
    VerificationResult,
)
from app.services.verification.service import (
    VerificationOutcome,
    verify_business_number,
)
from app.services.verification.status import BusinessVerificationStatus

__all__ = [
    "hash_business_number",
    "is_valid_business_number",
    "mask_business_number",
    "normalize_business_number",
    "NtsBusinessVerificationAdapter",
    "UnavailableBusinessVerificationAdapter",
    "build_business_verification_port",
    "BusinessVerificationPort",
    "VerificationResult",
    "VerificationOutcome",
    "verify_business_number",
    "BusinessVerificationStatus",
]
