"""운영자 식별 피드백(적합/부적합/보류) 입출력 스키마.

운영자가 공고별로 남기는 식별 판정을 ``NoticeEligibilityLabel(source="operator")``
로 저장하는 엔드포인트의 Pydantic 경계. verdict 허용값은
``eligibility_labeling.OPERATOR_VERDICTS`` 단일 출처를 따르며(매직값 금지 §4.5.1),
이 값 집합을 벗어난 verdict 는 422 로 거른다. 라벨 값(positive/negative/ambiguous)
매핑은 서비스(``eligibility_feedback.record_operator_label``)가 수행한다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.eligibility_labeling import OPERATOR_VERDICTS


class EligibilityFeedbackRequest(BaseModel):
    """운영자 식별 피드백 요청 — 어느 공고에 어떤 판정을 남길지."""

    project_id: int = Field(ge=1, description="식별 피드백 대상 프로젝트(공고) ID")
    verdict: str = Field(
        description="운영자 식별 판정: 적합/부적합/보류",
        json_schema_extra={"enum": list(OPERATOR_VERDICTS)},
    )

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, value: str) -> str:
        """verdict 를 허용 값 집합(단일 출처)으로 제한한다(위반 시 422)."""
        normalized = (value or "").strip()
        if normalized not in OPERATOR_VERDICTS:
            allowed = "/".join(OPERATOR_VERDICTS)
            raise ValueError(f"verdict 는 {allowed} 중 하나여야 합니다.")
        return normalized


class EligibilityFeedbackResponse(BaseModel):
    """저장된 operator 소스 라벨 요약(operator 컨텍스트 포함)."""

    model_config = ConfigDict(from_attributes=True)

    project_id: int
    verdict: str
    label: str
    source: str
    rationale: Optional[str] = None
    labeler_version: Optional[str] = None
    operator_id: int
