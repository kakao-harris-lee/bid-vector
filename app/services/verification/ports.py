"""사업자번호 검증 포트(계약)와 결과 DTO — 구현이 아니라 계약에 의존(§4.7-1).

호출부(서비스/라우터)는 이 추상 포트만 알고 구현(국세청 어댑터 / 미구성 어댑터)은
팩토리로 갈아끼운다. 결과 DTO 는 **원문 사업자번호를 담지 않는다**(masked payload 만).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerificationResult:
    """검증 1회의 결과 — 원문 번호/서비스키 없이 상태와 masked payload 만 담는다.

    * ``status`` — :class:`BusinessVerificationStatus` 의 값 문자열.
    * ``masked_payload`` — b_no 가 마스킹되고 서비스키가 제거된 상태 dict(로그/응답 안전).
    * ``checked`` — 외부 조회가 실제로 수행됐는지(키 미구성/미조회면 False).
    """

    status: str
    masked_payload: dict[str, Any] = field(default_factory=dict)
    checked: bool = False


class BusinessVerificationPort(ABC):
    """국세청 사업자등록정보 상태조회/진위확인 포트."""

    @abstractmethod
    def check_status(self, business_number: str) -> VerificationResult:
        """상태조회(계속/휴업/폐업). 실패는 raise 하지 않고 unknown 결과로 돌려준다."""
        raise NotImplementedError

    @abstractmethod
    def check_authenticity(
        self,
        business_number: str,
        *,
        start_date: str,
        representative_name: str,
    ) -> VerificationResult:
        """진위확인(개업일자·대표자명 대조). 실패는 raise 없이 unknown 으로 돌려준다."""
        raise NotImplementedError
