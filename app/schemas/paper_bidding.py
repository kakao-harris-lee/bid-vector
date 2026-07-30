"""Paper-bidding backtest run and background-job schemas.

응답 모델의 중첩 계약은 도메인별 모듈에 있다(§4.5-4 크기 한도):

* ``app/schemas/paper_bidding_items.py`` — 후보/정산/요약 "한 건" DTO
* ``app/schemas/paper_bidding_runs.py`` — run 요청 스냅샷 · 영속 run 레코드
* ``app/schemas/paper_bidding_audit.py`` — 데이터 준비도 audit DTO

요청 모델(``PaperBiddingRunRequest``/``ForwardPaperBiddingRunRequest``)은 외부에서
들어오는 입력이라 ``extra`` 기본값(``ignore``)에 의존하는 호출부가 검증되지 않았으므로
``BaseModel`` 을 유지한다. 응답 모델은 생산자가 이 저장소 안에 있으므로
``StrictModel`` 로 승격해 키 오타를 즉시 실패시킨다.
"""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

from app.schemas._base import StrictModel
from app.schemas.paper_bidding_audit import (
    BacktestDataAuditCategoryRow,
    BacktestDataAuditDateRange,
    BacktestDataAuditFilters,
    BacktestDataAuditTableCounts,
    BacktestDataAuditWindowCounts,
)
from app.schemas.paper_bidding_items import (
    PaperBiddingCandidateItem,
    PaperBiddingRunSummary,
    PaperBiddingSettlementItem,
)
from app.schemas.paper_bidding_runs import (
    PaperBiddingPaperBidRecord,
    PaperBiddingRunRequestSnapshot,
    PaperBiddingSettlementOverview,
    PaperBiddingSettlementRecord,
    PersistedPaperBiddingRunRequestSnapshot,
)


class BacktestDataAuditResponse(StrictModel):
    generated_at: str
    filters: BacktestDataAuditFilters
    table_counts: BacktestDataAuditTableCounts
    window_counts: BacktestDataAuditWindowCounts
    date_range: BacktestDataAuditDateRange
    category_breakdown: List[BacktestDataAuditCategoryRow] = Field(
        default_factory=list
    )


class PaperBiddingRunRequest(BaseModel):
    category: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=5000)
    scenario: Literal["conservative", "base", "aggressive"] = "base"
    strategy_version: str = "local-backtest"
    model_version: str = "current"
    cutoff_hours_before_deadline: int = Field(default=2, ge=0, le=168)
    history_limit: int = Field(default=80, ge=1, le=500)
    settle_actions: List[Literal["bid_now", "review", "skip"]] = Field(
        default_factory=lambda: ["bid_now"]
    )
    persist: bool = False


class ForwardPaperBiddingRunRequest(BaseModel):
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)
    scenario: Literal["conservative", "base", "aggressive"] = "base"
    strategy_version: str = "forward-paper"
    model_version: str = "current"
    history_limit: int = Field(default=80, ge=1, le=500)
    persist: bool = True


class PaperBiddingRunExecutionResponse(StrictModel):
    """historical/forward run 1회의 산출.

    서비스(``PaperBiddingBacktestService``)가 만드는 **내부 DTO 이자** API 응답
    모델이다. 같은 계약을 두 곳에 적어 두면 한쪽만 바뀌어 갈라지므로 단일 출처를
    유지한다(§4.5-6). 서비스 공개 메서드는 celery task 결과로도 쓰이므로 이 DTO 를
    ``model_dump(mode="json")`` 으로 직렬화해 반환한다.
    """

    run_id: Optional[int] = None
    request: PaperBiddingRunRequestSnapshot
    summary: PaperBiddingRunSummary
    items: List[PaperBiddingCandidateItem] = Field(default_factory=list)
    settlements: List[PaperBiddingSettlementItem] = Field(default_factory=list)


class PaperBiddingRunListItem(StrictModel):
    id: int
    operator_id: int
    status: str
    mode: str
    scenario: str
    category_filter: Optional[str] = None
    strategy_version: str
    model_version: str
    target_start_at: Optional[datetime] = None
    target_end_at: Optional[datetime] = None
    data_cutoff_policy: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    candidate_count: int = Field(ge=0)
    paper_bid_count: int = Field(ge=0)
    settled_count: int = Field(ge=0)
    settlement_overview: PaperBiddingSettlementOverview
    summary: PaperBiddingRunSummary


class PaperBiddingRunListResponse(StrictModel):
    operator_id: int
    returned_count: int = Field(ge=0)
    limit: int = Field(ge=1)
    items: List[PaperBiddingRunListItem] = Field(default_factory=list)


class PaperBiddingRunDetailResponse(PaperBiddingRunListItem):
    # 저장된 요청 스냅샷을 해석할 수 없으면(어휘가 바뀌기 전 시절의 run) ``None`` 을
    # 유지하고, 부분 복원은 미기록 필드를 ``null`` 로 남긴다 — 감사 메타데이터라 "부재"가
    # 정직하고, 0 으로 채우면 그 run 이 실제로 limit=0 으로 돌았다는 오독을 만든다.
    # 그래서 생산 union 이 아니라 ``Persisted*`` union 을 쓴다.
    request: Optional[PersistedPaperBiddingRunRequestSnapshot] = None
    paper_bids: List[PaperBiddingPaperBidRecord] = Field(default_factory=list)
    settlements: List[PaperBiddingSettlementRecord] = Field(default_factory=list)

    @classmethod
    def from_list_item(
        cls,
        item: PaperBiddingRunListItem,
        *,
        request: Optional[PersistedPaperBiddingRunRequestSnapshot],
        paper_bids: List[PaperBiddingPaperBidRecord],
        settlements: List[PaperBiddingSettlementRecord],
    ) -> "PaperBiddingRunDetailResponse":
        """목록 항목에 상세 전용 필드를 얹어 상세 응답을 만든다.

        상세는 목록의 상위집합이라 공통 필드를 두 번 적으면 갈라진다. ``dict(item)``
        은 **얕은** 필드 전개라 중첩 DTO(``settlement_overview``/``summary``)가
        인스턴스로 그대로 넘어간다 — ``model_dump()`` 로 dict 로 내렸다가 다시 검증하는
        릴레이를 만들지 않는다(그 릴레이는 중첩 계약을 우회할 틈이 된다).
        """
        return cls(
            **dict(item),
            request=request,
            paper_bids=paper_bids,
            settlements=settlements,
        )


class PaperBiddingSummaryResponse(StrictModel):
    operator_id: int
    latest_run: Optional[PaperBiddingRunListItem] = None
    run_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    # 실행이 없으면 빈(0) 요약을 준다. 종전 ``{}`` 와 달리 항상 객체 모양을 유지해
    # 소비처의 ``summary.<field>`` 접근이 깨지지 않게 한다.
    latest_summary: PaperBiddingRunSummary = Field(
        default_factory=PaperBiddingRunSummary
    )


class BackgroundJobResponse(BaseModel):
    task_name: str
    status: str
    detail: str
