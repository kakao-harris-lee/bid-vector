"""백테스트 데이터 준비도 audit DTO.

종전 ``BacktestDataAuditResponse`` 는 ``filters``/``table_counts``/``window_counts``/
``date_range``/``category_breakdown`` 이 모두 bare ``dict`` 여서 ``response_model`` 이
있어도 실효 검증이 0 이었다(생산자 ``BacktestDataAuditService.build_report`` 의 키
오타가 그대로 프론트로 흘렀다). 계약을 여기로 올린다.

시각은 isoformat 문자열이다 — 생산자가 이미 ``.isoformat()`` 으로 넘기고 있고, 경계
산출을 바꾸지 않기 위해 그대로 문자열 계약으로 고정한다.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas._base import StrictModel

__all__ = [
    "BacktestDataAuditCategoryRow",
    "BacktestDataAuditDateRange",
    "BacktestDataAuditFilters",
    "BacktestDataAuditTableCounts",
    "BacktestDataAuditWindowCounts",
]


class BacktestDataAuditFilters(StrictModel):
    """리포트를 만든 조회 조건(그대로 되돌려 준다)."""

    categories: list[str] = Field(default_factory=list)
    start_at: str | None = None
    end_at: str | None = None


class BacktestDataAuditTableCounts(StrictModel):
    """백테스트가 의존하는 테이블별 전체 행 수."""

    projects_total: int
    projects_active_open_or_re_notice: int
    historical_total: int
    historical_with_bid_rate: int
    historical_with_project_id: int
    tender_results_total: int
    tender_results_usable_awards: int
    price_predictions_total: int
    bid_decisions_total: int
    bid_decisions_submitted: int
    bids_total: int


class BacktestDataAuditWindowCounts(StrictModel):
    """요청 창(카테고리·기간) 안에서 실제로 쓸 수 있는 개찰 표본 수."""

    usable_award_count: int
    pending_or_opening_snapshot_count: int
    distinct_project_count: int


class BacktestDataAuditDateRange(StrictModel):
    """창 안 개찰 공고의 안내일 최소/최대(isoformat, 표본 없으면 None)."""

    award_announced_min: str | None = None
    award_announced_max: str | None = None


class BacktestDataAuditCategoryRow(StrictModel):
    """카테고리별 사용 가능 개찰 표본 분포 한 행."""

    category: str
    usable_award_count: int
    distinct_project_count: int
