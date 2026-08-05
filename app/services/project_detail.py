"""공고 상세 조립 — ORM 행 + 투찰 기준금액(기초금액) 해석.

라우터는 얇게 두고(§4), 상세 응답이 "추정가격만 보여 주던" 상태를 벗어나는 데 필요한
기초금액 해석은 여기서 한다. 해석 자체는 ``describe_notice_bid_base`` 단일 출처를 쓰며
이 모듈은 그 결과를 응답 DTO 로 옮기기만 한다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.models import Project
from app.schemas.project import ProjectDetailResponse
from app.services.bid_base import describe_notice_bid_base


def build_project_detail(db: Session, project: Project) -> ProjectDetailResponse:
    """ORM 공고를 상세 응답으로 옮기고 투찰 기준금액을 덧붙인다.

    ``model_validate`` 로 ORM 필드를 옮긴 뒤 기초금액 필드만 갈아 끼운다 — 필드 목록을
    손으로 다시 적으면 ``ProjectResponse`` 가 바뀔 때 상세만 조용히 뒤처진다.
    """
    bid_base = describe_notice_bid_base(db, project)
    detail = ProjectDetailResponse.model_validate(project)
    return detail.model_copy(
        update={
            "bid_base_amount": float(bid_base.amount),
            "bid_base_source": bid_base.source,
            "bid_base_to_estimate_ratio": bid_base.to_estimate_ratio,
        }
    )
