"""의사결정 증적 샘플(decision evidence samples) export route.

Roadmap 0단계 L87 "예측/추천 API 응답 샘플 캡처(증적 보관)".

Thin router — aggregation lives in ``DecisionSampleService``. Exposes a single
read-only endpoint that lists recent served recommendations(``BidDecisionRecord``)
joined with the latest served prediction(``PricePrediction``) per project, for
audit-evidence purposes. 정산(settled) 항목만 보여주는 정확도 리포트와 달리, 정산
여부와 무관하게 시스템이 산출·기록한 추천/예측 자체를 노출한다.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_operator_optional, resolve_target_operator
from app.core.single_user import ensure_operator_account
from app.models.models import User
from app.schemas.decision_samples import DecisionSamplesResponse
from app.services.decision_samples import DecisionSampleService

router = APIRouter()


def _resolve_samples_operator(
    db: Session,
    current_operator: User | None,
    operator_id: int | None,
) -> User:
    if current_operator is None:
        fallback = ensure_operator_account(db)
        if operator_id is None or int(operator_id) == int(fallback.id):
            return fallback
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view another operator's data",
        )
    return resolve_target_operator(db, current_operator, operator_id)


def _with_current_operator(payload: dict, operator: User) -> dict:
    payload["current_operator_id"] = int(operator.id)
    payload["current_operator_username"] = str(operator.username or "")
    return payload


@router.get(
    "/decision-samples",
    response_model=DecisionSamplesResponse,
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/csv": {},
            }
        }
    },
)
def get_decision_samples(
    days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="조회 기간(일). created_at >= now-days 범위의 추천 기록만 포함.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="최대 샘플 수(최근 순). 상한 도달 시 truncated=true.",
    ),
    format: Literal["json", "csv"] = Query(
        default="json",
        description="응답 포맷. json(기본, 구조화) / csv(다운로드).",
    ),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """List recent served recommendations + their latest predictions (audit evidence).

    Resolved operator 기준. 정산 결과가 아니라 시스템이 산출·기록한 추천/예측
    증적을 최근 순으로 반환한다. ``probability_score`` 는 가격 적합도(추정)이지
    P(낙찰)이 아니다(§1.5). ``format=csv`` 는 평탄화된 다운로드용 CSV 를 반환한다.
    """
    operator = _resolve_samples_operator(db, current_operator, operator_id)
    service = DecisionSampleService()
    payload = service.build_samples(db, days=days, limit=limit, operator=operator)
    payload = _with_current_operator(payload, operator)

    if format == "csv":
        return Response(
            content=service.render_csv(payload),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="decision-samples.csv"'
            },
        )

    return payload
