"""페이퍼 투찰 run 단위 DTO — 요청 스냅샷 · run 산출 · 영속화된 run 레코드.

방어적 DTO 규율 Phase 1. 종전에는 run 요청 스냅샷과 run 산출이 무타입 ``dict`` 로
만들어져 ``PaperBidRun.request_payload`` / ``result_payload`` 에 ``json.dumps`` 로
직행하고, 읽을 때는 ``json.loads`` 후 실패하면 ``{}`` 로 삼켰다. 계약을 여기로 올려
직렬화/복원을 ``model_dump_json()`` / ``model_validate_json()`` 으로 통일한다
(:mod:`app.services.paper_bidding_run_payload` 가 그 단일 경로다).

``Persisted*`` 서브클래스는 **되읽기 전용**이다: 산출 DTO 는 ``extra="forbid"`` 여야
오타 키를 잡지만, 과거 실행이 남긴 payload 를 forbid 로 읽으면 오래된 run 하나가
목록 API 전체를 500 으로 만든다. 그래서 복원 경로만 미지 키를 무시한다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from app.schemas._base import StrictModel
from app.schemas.paper_bidding_items import (
    PaperBiddingRunSummary,
    PaperBiddingSettlementItem,
)

__all__ = [
    "SETTLEMENT_BASIS",
    "ForwardPaperBiddingRunRequestSnapshot",
    "ForwardPaperRunParams",
    "ForwardSettlementRunResult",
    "HistoricalBacktestRunRequestSnapshot",
    "PaperBiddingPaperBidRecord",
    "PaperBiddingRunRequestSnapshot",
    "PaperBiddingSettlementOverview",
    "PaperBiddingSettlementRecord",
    "PersistedForwardPaperBiddingRunRequestSnapshot",
    "PersistedHistoricalBacktestRunRequestSnapshot",
    "PersistedPaperBiddingRunRequestSnapshot",
]

# 정산 판정의 근거를 응답에 명시하는 고정 문구(§4.5-1: 함수 안 리터럴 금지).
SETTLEMENT_BASIS = "TenderResult.winning_amount > 0 matched by project_id"


class HistoricalBacktestRunRequestSnapshot(StrictModel):
    """historical replay run 의 정규화된 요청 스냅샷 (생산 경로).

    시각은 ``datetime`` 이 아니라 isoformat 문자열이다 — 종전 산출(``.isoformat()``,
    ``+00:00``)을 유지하기 위함이다. 생산자는 항상 모든 필드를 채우며 그 사실은
    characterization 골든(정확한 12키 payload)이 고정한다. 과거 payload 복원은 이
    모델이 아니라 ``Persisted*`` 변종이 담당한다(미기록 필드는 ``null`` 로 보존).
    """

    category: str | None = None
    # 빈 튜플이면 카테고리 추가 스코핑 없음(전략 focus 폴백 결과 포함).
    award_categories: list[str] = Field(default_factory=list)
    start_at: str | None = None
    end_at: str | None = None
    limit: int = 0
    scenario: str = ""
    strategy_version: str = ""
    model_version: str = ""
    cutoff_hours_before_deadline: int = 0
    history_limit: int = 0
    settle_actions: list[str] = Field(default_factory=list)
    persist: bool = False


class ForwardPaperBiddingRunRequestSnapshot(StrictModel):
    """forward paper run 의 정규화된 요청 스냅샷.

    historical 과 키 집합이 다르다(정산 창/컷오프가 없고 실행 시각이 컷오프다).
    두 모양을 한 모델로 합치면 없는 키가 ``null`` 로 산출에 끼어들어 경계 계약이
    바뀌므로 분리해 둔다.

    ``data_cutoff_at`` 은 **필수**다. 생산자가 항상 실행 시각을 채우고, 같은 키가
    historical 모델에서는 ``extra="forbid"`` 로 거부되므로 두 모양의 배타성이 "우연히
    겹치는 키가 없어서"가 아니라 **구조적으로** 성립한다(공유 키만 있는 payload 는
    forward 로 검증될 수 없다).
    """

    category: str | None = None
    limit: int = 0
    scenario: str = ""
    strategy_version: str = ""
    model_version: str = ""
    history_limit: int = 0
    persist: bool = False
    data_cutoff_at: str


class ForwardPaperRunParams(StrictModel):
    """forward paper run 실행 인자 (이미 정규화된 값).

    공개 진입점이 받은 kwargs 를 정규화(``limit``/``scenario``)한 결과를 담아 내부
    구현으로 넘긴다. 인자 10개를 메서드마다 다시 늘어놓으면 서명이 함수 본문보다
    길어지고 한 곳만 빠뜨리는 사고가 나므로 하나의 계약으로 묶는다(§4.5-1).
    """

    operator_id: int | None = None
    category: str | None = None
    limit: int
    scenario: str
    strategy_version: str
    model_version: str
    history_limit: int
    persist: bool


# 생산 경로 union: ``_create_run`` 인자와 run 산출 응답의 ``request`` 타입.
# ``extra="forbid"`` + forward 의 필수 ``data_cutoff_at`` 조합으로 두 모양은 구조적으로
# 배타적이다(historical 전용 키가 forward 검증을 실패시키고 그 역도 성립).
PaperBiddingRunRequestSnapshot = (
    HistoricalBacktestRunRequestSnapshot | ForwardPaperBiddingRunRequestSnapshot
)


class PersistedHistoricalBacktestRunRequestSnapshot(
    HistoricalBacktestRunRequestSnapshot
):
    """저장된 historical ``request_payload`` 복원용.

    모든 필드가 ``X | None = None`` 이다. 생산 모델의 기본값(``limit=0``,
    ``persist=False``, ``scenario=""``)을 그대로 쓰면 ``{"limit": 5}`` 만 남은 과거
    payload 를 읽었을 때 **기록되지 않은 값이 기록된 값처럼 날조**된다("이 run 은
    persist=false 로 돌았다"는 오독). 미기록은 ``null`` 로 보존하는 것이 정직하다.
    """

    model_config = ConfigDict(extra="ignore")

    category: str | None = None
    award_categories: list[str] | None = None
    start_at: str | None = None
    end_at: str | None = None
    limit: int | None = None
    scenario: str | None = None
    strategy_version: str | None = None
    model_version: str | None = None
    cutoff_hours_before_deadline: int | None = None
    history_limit: int | None = None
    settle_actions: list[str] | None = None
    persist: bool | None = None


class PersistedForwardPaperBiddingRunRequestSnapshot(
    ForwardPaperBiddingRunRequestSnapshot
):
    """저장된 forward ``request_payload`` 복원용.

    :class:`PersistedHistoricalBacktestRunRequestSnapshot` 과 같은 이유로 모든 필드가
    ``X | None = None`` 이다(생산 모델에서 필수인 ``data_cutoff_at`` 도 과거 payload 에
    없을 수 있으므로 복원 경로에서는 옵셔널).
    """

    model_config = ConfigDict(extra="ignore")

    category: str | None = None
    limit: int | None = None
    scenario: str | None = None
    strategy_version: str | None = None
    model_version: str | None = None
    history_limit: int | None = None
    persist: bool | None = None
    data_cutoff_at: str | None = None


# 복원 경로 union: 영속 run 상세 응답의 ``request`` 타입. 생산 union 과 분리한 이유는
# 위 두 클래스의 docstring 과 같다 — 복원은 필드 단위 ``null``(미기록)을 표현해야 하고,
# 생산 계약은 그 느슨함을 물려받지 않아야 한다.
PersistedPaperBiddingRunRequestSnapshot = (
    PersistedHistoricalBacktestRunRequestSnapshot
    | PersistedForwardPaperBiddingRunRequestSnapshot
)


class ForwardSettlementRunResult(StrictModel):
    """마감이 지난 forward 페이퍼 투찰을 뒤늦게 정산한 스캔 결과."""

    operator_id: int | None
    scanned_count: int
    settled_count: int
    skipped_count: int
    limit: int
    persist: bool
    summary: PaperBiddingRunSummary
    settlements: list[PaperBiddingSettlementItem]


class PaperBiddingSettlementOverview(StrictModel):
    """한 run 의 페이퍼 투찰이 최종 결과로 정산 가능한지 요약."""

    status: str
    label: str
    detail: str
    settlement_basis: str = SETTLEMENT_BASIS
    paper_bid_count: int
    settled_count: int
    unsettled_count: int
    ready_to_settle_count: int
    waiting_result_count: int
    before_deadline_count: int
    missing_deadline_count: int
    next_confirmable_at: datetime | None
    next_deadline_at: datetime | None
    oldest_waiting_deadline_at: datetime | None
    latest_settled_at: datetime | None


class PaperBiddingPaperBidRecord(StrictModel):
    """영속화된 ``PaperBid`` 한 행의 읽기 표현.

    산출 DTO(:class:`PaperBiddingCandidateItem`)와 키 집합이 다르다: 여기에는 행
    식별자(``id``/``run_id``)와 ``created_at`` 이 있고, 후보 시점 스코어 일부는 없다.
    컬럼이 전부 nullable 이므로 문자열 필드는 ``str | None`` 이다.
    """

    id: int
    run_id: int
    project_id: int
    project_title: str | None
    notice_number: str | None
    category: str | None
    action: str | None
    decision_status: str | None
    data_cutoff_at: datetime | None
    paper_bid_amount: float
    paper_bid_rate: float
    scenario: str | None
    priority_score: float
    probability_score: float
    matched_score: float
    predicted_price: float
    predicted_bid_rate: float
    confidence_score: float
    predictor_name: str | None
    input_snapshot_hash: str | None
    created_at: datetime | None


class PaperBiddingSettlementRecord(StrictModel):
    """영속화된 ``PaperBidSettlement`` 한 행의 읽기 표현.

    판정 필드는 ``Literal`` 이 아니라 ``str | None`` 이다 — 어휘가 바뀌기 전 시절의
    행을 읽어도 목록 API 가 죽지 않아야 한다. 새로 만드는 정산 산출은
    :class:`PaperBiddingSettlementItem` 에서 ``Literal`` 로 고정된다.
    """

    id: int
    paper_bid_id: int
    tender_result_id: int | None
    result_status: str | None
    winning_company: str | None
    winning_amount: float
    winning_rate: float
    amount_delta: float
    absolute_error_rate: float
    bid_rate_delta: float
    absolute_bid_rate_error: float
    price_close: bool
    price_competitive: bool
    would_have_won_price_only: str | None
    would_have_won_final: str | None
    settlement_reason: str | None
    settled_at: datetime | None
