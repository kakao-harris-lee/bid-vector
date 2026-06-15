"""투찰서 초안(bid-form draft) export route (PR8 / item 4-B).

Thin router — aggregation/mapping lives in ``BidFormDraftService`` (which reuses
the PR7 ``BidSummaryService``). Exposes a single read-only endpoint that returns
the 나라장터 투찰서 입력 항목 매핑 초안 so the operator can type the values into
KONEPS **by hand**.

⚠ 자동 제출은 명시적으로 범위 밖입니다. This endpoint NEVER calls KONEPS and performs
NO automated 투찰서 submission — the operator submits directly on 나라장터.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.bid_form_draft import BidFormDraftResponse
from app.services.bid_form_draft import BidFormDraftService

router = APIRouter()


@router.get(
    "/bid-decisions/{decision_record_id}/bid-form-draft",
    response_model=BidFormDraftResponse,
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/csv": {},
                "text/plain": {},
            }
        }
    },
)
def get_bid_form_draft(
    decision_record_id: int,
    format: Literal["json", "csv", "text"] = Query(
        default="json",
        description="응답 포맷. json(기본, 구조화) / csv / text(평문).",
    ),
    db: Session = Depends(get_db),
):
    """Build a 나라장터 투찰서 초안 mapped onto KONEPS 입력 항목 for one decision.

    Reuses the PR7 summary aggregation, then maps recommended 투찰금액/투찰률,
    공고 메타, 적격여부(추정), and the 카테고리 낙찰하한율(참고) onto field-label/value
    pairs. 자동 제출은 하지 않습니다 — 운영자가 직접 나라장터에 입력·제출합니다.

    ``format=csv`` / ``format=text`` return downloadable / printable renders of the
    same data. 404 when the record id is unknown (or belongs to a different
    operator) / the linked project is missing.
    """
    service = BidFormDraftService()
    try:
        draft = service.build_draft(db, decision_record_id=decision_record_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    if format == "csv":
        filename = f"bid-form-draft-{decision_record_id}.csv"
        return Response(
            content=service.render_csv(draft),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if format == "text":
        return Response(
            content=service.render_text(draft),
            media_type="text/plain; charset=utf-8",
        )

    return draft
