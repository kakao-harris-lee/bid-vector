"""반자동 온보딩 후보 조회 라우터(읽기 전용).

operator 라우터(``app/api/operator.py``)가 이미 크므로(§4.5.4) 온보딩 경계를 별
모듈로 분리하고 ``/operator`` prefix 아래 등록한다. 라우터는 얇게 유지하고
(§4/§4.5.5) 도메인 로직은 ``services.onboarding`` 에 위임한다. 인증은 기존 operator
읽기 엔드포인트와 동일한 의존성(``get_current_operator_optional`` +
``resolve_read_operator``)을 사용해 보안 정책(canonical fallback / 403 / 404)을
공유한다. 이 엔드포인트는 공용 공고만 읽고 operator 데이터는 쓰지 않는다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_operator_optional
from app.core.single_user import resolve_read_operator
from app.models.models import User
from app.schemas.onboarding import OnboardingSuggestionsResponse
from app.services.onboarding import OnboardingSeed, suggest_onboarding_fields

router = APIRouter()


@router.get("/onboarding-suggestions", response_model=OnboardingSuggestionsResponse)
def get_onboarding_suggestions(
    keywords: list[str] | None = Query(
        default=None,
        description="역추천 seed 키워드(반복 파라미터). 최소 1개 필요",
    ),
    region: str | None = Query(default=None, description="지역 힌트(선택)"),
    min_budget: float | None = Query(default=None, ge=0, description="예산 하한 힌트(선택)"),
    max_budget: float | None = Query(default=None, ge=0, description="예산 상한 힌트(선택)"),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """내부 공고에서 회사 프로필/전략 필드 후보를 역추천한다(persist 없음)."""
    target = resolve_read_operator(db, current_operator, operator_id)
    seed = OnboardingSeed.from_inputs(
        keywords,
        region=region,
        min_budget=min_budget,
        max_budget=max_budget,
    )
    if not seed.keywords:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="keywords 는 최소 1개 이상 필요합니다.",
        )

    bundle = suggest_onboarding_fields(db, seed=seed)
    return {
        "keywords": list(seed.keywords),
        "matched_notice_count": bundle.matched_notice_count,
        "diagnostics": bundle.diagnostics,
        "profile": [item.to_dict() for item in bundle.profile],
        "strategy": [item.to_dict() for item in bundle.strategy],
        "current_operator_id": int(target.id),
        "current_operator_username": str(target.username or ""),
    }
