"""Shared import surface and honesty constants for the schemas package."""

try:  # pragma: no cover - optional dependency fallback
    import email_validator  # noqa: F401
    from pydantic import EmailStr
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    EmailStr = str


# 표시 정직화: 이 점수는 "가격 적합도(추정)" 신호이지 실제 낙찰 확률 P(낙찰)이 아니다.
# 적격성 게이트(would_have_won_final)는 별도로 판정된다. 학습/보정 시에는
# summary.probability_calibration 으로 정산 결과에 보정될 수 있으나 라벨은 별개다.
_PROBABILITY_SCORE_DESCRIPTION = (
    "가격 적합도(추정) — P(낙찰) 아님(would_have_won_final 게이트 별도)"
)

__all__ = ["EmailStr", "_PROBABILITY_SCORE_DESCRIPTION"]
