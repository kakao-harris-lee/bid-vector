"""사업자번호 검증 라우터 — 얇은 경계(§4), 로직은 services.verification 에 위임.

operator 라우터가 이미 크므로(§4.5.4) 검증 경계를 별 모듈로 분리하고 ``/operator``
prefix 아래 등록한다. write 스코프는 PUT ``/operator/profile`` 과 동일한
``resolve_write_operator`` (self-only, cross-operator 403)를 재사용해 요청 operator 자신의
``CompanyProfile`` 한 행에만 반영한다(per-operator 격리).

원문 사업자번호는 요청 body 로만 들어오고(transient) 응답·로그·저장에 남지 않는다.
키가 없으면 서비스가 graceful 하게 status='unknown' 을 돌려주므로 HTTP 200 이다(오류 아님).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_operator_optional
from app.core.single_user import resolve_write_operator
from app.models.models import User
from app.schemas.business_verification import (
    BusinessVerificationRequest,
    BusinessVerificationResponse,
)
from app.services.verification import verify_business_number

router = APIRouter()


@router.post("/business-verification", response_model=BusinessVerificationResponse)
def verify_operator_business_number(
    request: BusinessVerificationRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """사업자번호 상태조회/진위확인 후 결과를 현재 operator 프로필에 반영한다.

    ``start_date`` + ``representative_name`` 이 모두 오면 진위확인, 아니면 상태조회다.
    서비스키가 없으면 외부 호출 없이 status='unknown'(HTTP 200)으로 끝난다. 응답은
    masked 번호/상태만 담고 원문 번호·서비스키를 포함하지 않는다(§8).
    """
    operator = resolve_write_operator(db, current_operator, operator_id)
    outcome = verify_business_number(
        db,
        operator=operator,
        business_number=request.business_number,
        start_date=request.start_date,
        representative_name=request.representative_name,
    )
    return BusinessVerificationResponse(
        status=outcome.status,
        masked_number=outcome.masked_number,
        verified_at=outcome.verified_at,
        detail=outcome.detail,
        current_operator_id=int(operator.id),
        current_operator_username=str(operator.username or ""),
    )
