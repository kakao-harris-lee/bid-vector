"""사업자번호 정규화·해시·마스킹 — 순수 함수(§4.7-4, I/O 없음).

원문 사업자번호는 어디에도 저장하지 않는다(설계 §비기능, CLAUDE.md §8). 저장은 pepper
HMAC-SHA256 해시만, 표시는 masked 번호만 허용한다. 이 모듈은 DB·네트워크·전역에 의존하지
않는 순수 계산만 담아 값 테이블로 단위 검증이 끝난다.

보안 note: ``hash_business_number`` 는 HMAC-SHA256 을 쓴다. 유효 사업자번호 공간이
작아(~10^8) bare SHA256 은 무차별 대입이 자명하므로, 서버 pepper 를 HMAC 키로 써
사전공격 저항을 확보한다. pepper 가 빈 문자열이면 길이 0 HMAC 키로 여전히 결정적이라
중복 조회에는 문제 없지만 무차별 대입 저항이 약해진다 — 운영에서는 실제 pepper 설정을
권장한다(문서화된 known-limitation; 다른 시크릿과 결합하지 않는다).
"""

from __future__ import annotations

import hashlib
import hmac
import re

_NON_DIGIT = re.compile(r"\D+")

# 한국 사업자등록번호 = 10 자리(3-2-5). 마스킹은 앞 5 자리(3-2)만 노출하고 뒤 5 자리를
# 가린다(예: ``123-45-*****``).
_VISIBLE_PREFIX_LEN = 5
_MASK_SUFFIX = "*****"


def normalize_business_number(number: str | None) -> str:
    """사업자번호에서 숫자만 남긴다(하이픈/공백/기타 제거). None/빈 값 → 빈 문자열."""
    if not number:
        return ""
    return _NON_DIGIT.sub("", str(number))


def hash_business_number(number: str | None, pepper: str | None) -> str:
    """정규화(숫자만) 사업자번호의 HMAC-SHA256 해시(hex)를 반환한다.

    ``pepper`` 는 HMAC 키다. 빈 pepper 는 유효한 길이 0 키로 결정적이지만 무차별 대입
    저항이 약하다(모듈 docstring 참고). 결과는 원문을 복원할 수 없는 64 자 hex 다.
    """
    digits = normalize_business_number(number)
    return hmac.new(
        (pepper or "").encode("utf-8"),
        digits.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mask_business_number(number: str | None) -> str:
    """표시용 masked 사업자번호를 반환한다(원문 미노출).

    10 자리 정상 번호 ``1234567890`` → ``123-45-*****``. 자리수가 모자라거나 비정상이면
    가용한 앞자리만 노출하고 나머지는 가린다(빈 값 → ``*****``). 어떤 경우에도 뒤 5 자리는
    노출하지 않는다.
    """
    digits = normalize_business_number(number)
    if not digits:
        return _MASK_SUFFIX
    prefix = digits[:_VISIBLE_PREFIX_LEN]
    if len(prefix) <= 3:
        visible = prefix
    else:
        visible = f"{prefix[:3]}-{prefix[3:]}"
    return f"{visible}-{_MASK_SUFFIX}"


def is_valid_business_number(number: str | None) -> bool:
    """정규화 후 정확히 10 자리 숫자인지 검사한다(형식 검증용, 순수)."""
    return len(normalize_business_number(number)) == 10
