"""Eligibility-feedback domain impl for the single-operator API.

Pure move (#295 pattern) from ``app/api/operator.py``: the ``@router`` entry
stays in operator.py (thin), forwarding the resolved operator plus the raw
request here. No behaviour change.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.operator_common import _SYNTHETIC_USERNAME_PREFIX
from app.models.models import Project, User
from app.schemas.eligibility_feedback import (
    EligibilityFeedbackRequest,
    EligibilityFeedbackResponse,
)
from app.services.eligibility_feedback import record_operator_label


def _reject_synthetic_labeler(operator: User) -> None:
    """synthetic-* 검증 계정의 식별 정답 라벨 write 를 거부한다(403).

    operator 라벨 유니크가 ``(project_id, source="operator")`` 로 operator 차원이
    없어, synthetic operator 제출이 canonical 운영자 피드백과 같은 행을 덮어써
    precision/recall 정답 세트를 오염시킨다. 핵심 불변식(synthetic 데이터를 canonical
    에 섞지 않음, CLAUDE.md §8)을 지키려 정답 세트는 실제 운영자 피드백만 받는다.
    판별은 파일 내 단일 출처 ``_SYNTHETIC_USERNAME_PREFIX`` 를 재사용한다.
    """
    username = operator.username or ""
    if username.startswith(_SYNTHETIC_USERNAME_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="synthetic operator는 식별 정답 라벨을 남길 수 없습니다.",
        )


def submit_eligibility_feedback_impl(
    request: EligibilityFeedbackRequest,
    operator: User,
    db: Session,
) -> EligibilityFeedbackResponse:
    _reject_synthetic_labeler(operator)
    project = (
        db.query(Project).filter(Project.id == request.project_id).one_or_none()
    )
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    label = record_operator_label(
        db, project=project, verdict=request.verdict, operator=operator
    )
    db.commit()
    db.refresh(label)
    return EligibilityFeedbackResponse(
        project_id=int(label.project_id),
        verdict=request.verdict,
        label=str(label.label),
        source=str(label.source),
        rationale=label.rationale,
        labeler_version=label.labeler_version,
        operator_id=int(operator.id),
    )
