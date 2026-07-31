"""페이퍼 투찰 후보 · 정산 · run 요약의 "한 건" 단위 DTO.

방어적 DTO 규율 Phase 1. 종전 이 경계는 무타입 ``dict`` 릴레이였다:
``_build_candidate_payload`` 가 31키 dict 를 만들고, ``_build_settlement_item`` 이
그중 5키를 ``item["paper_bid_amount"]`` 식으로 필수 인덱싱하고, ``_build_summary`` 는
같은 키를 어떤 곳은 ``item["price_close"]`` 로 어떤 곳은 ``item.get(...)`` 로 읽어서
**무엇이 필수이고 무엇이 옵셔널인지 코드로 판별할 수 없었다**. 계약을 여기로 올려
필수/옵셔널을 타입으로 못박는다.

필수/옵셔널 판정 근거:

* 생산자(``_build_candidate_payload`` / ``_build_settlement_item`` / ``_build_summary``)가
  **항상 채우는 키는 필수 필드**다. 기본값을 주면 생산자가 키를 빠뜨려도 조용히
  통과하므로, 요약(``PaperBiddingRunSummary``)만 예외적으로 기본값을 갖는다 —
  과거 실행이 남긴 ``PaperBidRun.result_payload`` 를 되읽어야 하기 때문이다
  (:class:`PersistedPaperBiddingRunSummary`). 생산 경로의 누락은
  ``model_fields_set`` 계약 테스트로 잡는다.
* ORM 컬럼이 nullable 이라 ``None`` 이 실제로 흐르는 값(``project_title``,
  ``notice_number``, ``category``, ``issuing_agency``, ``winning_company``,
  ``estimated_price``, ``minimum_bid_price``)만 ``X | None`` 이다.
* 시각은 ORM ``datetime`` 이 아니라 **isoformat 문자열**로 남긴다. 종전 산출이
  ``datetime.isoformat()`` (``+00:00``) 이었고 pydantic 의 datetime JSON 직렬화는
  ``Z`` 표기라 산출이 흔들리기 때문이다(경계 산출 불변이 우선).

정직 명세(CLAUDE.md §2): ``would_have_won_price_only`` 는 가격 근접 기반 추정이고
``would_have_won_final`` 은 예정가/낙찰하한가가 없으면 ``"unknown"`` 으로 남는다.
두 어휘를 ``Literal`` 로 고정해 새 판정값이 조용히 추가되지 못하게 한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from app.core.constants import PaperBidAction
from app.schemas._base import StrictModel

__all__ = [
    "PaperBidAction",
    "PaperBidDecisionStatus",
    "PaperBiddingCandidateItem",
    "PaperBiddingRunSummary",
    "PaperBiddingSettlementInput",
    "PaperBiddingSettlementItem",
    "PersistedPaperBiddingRunSummary",
    "WouldHaveWonFinal",
    "WouldHaveWonPriceOnly",
]


# --- 어휘 (§4.5-1: 값 집합은 선언적으로) -----------------------------------------
# 결정 게이트 사다리(app/services/allocation_core.py)가 낼 수 있는 action 전체
# (``PaperBidAction``)는 ``app/core/constants.py`` 가 단일 출처이며 위에서 import 해
# ``__all__`` 로 re-export 만 한다.

# ``_decision_status_for_action`` 의 상(相) 전체.
PaperBidDecisionStatus = Literal["planned", "reviewing", "skipped"]
# 가격 근접 기반 추정 낙찰(실제 낙찰이 아니다).
WouldHaveWonPriceOnly = Literal["plausible", "competitive", "unlikely"]
# 낙찰하한/적격 게이트까지 적용한 추정. 예정가 부재 시 "unknown" 을 유지한다.
WouldHaveWonFinal = Literal[
    "unknown", "disqualified", "eligible_favorable", "eligible_but_outbid"
]


class PaperBiddingCandidateItem(StrictModel):
    """한 공고에 대한 페이퍼 투찰 후보 1건.

    필드 순서는 종전 ``_build_candidate_payload`` dict 리터럴 순서를 그대로 따른다
    (``model_dump`` 산출 키 순서 유지).
    """

    project_id: int
    project_title: str | None
    notice_number: str | None
    category: str | None
    issuing_agency: str | None
    # 예측에 쓴 데이터 컷오프(isoformat). 시간 누수 차단의 기준선이라 필수다.
    data_cutoff_at: str
    deadline: str | None
    budget_estimate: float
    scenario: str
    action: PaperBidAction
    decision_status: PaperBidDecisionStatus
    paper_bid_amount: float
    paper_bid_rate: float
    priority_score: float
    probability_score: float
    matched_score: float
    predicted_price: float
    predicted_bid_rate: float
    price_range_min: float
    price_range_max: float
    confidence_score: float
    predictor_name: str
    predictor_family: str
    model_version: str
    strategy_version: str
    historical_sample_size: int
    history_ids: list[int]
    input_snapshot_hash: str
    matched_score_source: str
    match_reasons: list[str]
    reasoning: str


class PaperBiddingSettlementInput(StrictModel):
    """정산 점수 계산이 후보에서 **실제로 읽는** 필드만 담은 좁은 입력 포트.

    ``_build_settlement_item`` 은 후보 31키 중 5키만 읽는다. 전체 후보를 요구하면
    ``_paper_bid_to_settlement_input`` (영속화된 ``PaperBid`` 를 뒤늦게 정산하는
    forward 경로) 이 나머지 26키를 가짜로 채워야 하므로, 계약을 읽는 만큼으로
    좁힌다.

    ``paper_bid_amount``/``paper_bid_rate`` 는 종전 ``float(item[...] or 0.0)`` 로
    ``None`` 을 0.0 으로 흡수했지만, 두 생산 경로 모두 항상 float 을 넣으므로
    필수 float 으로 좁힌다(0.0 폴백은 유지 — 값 자체가 0 이면 종전과 동일).
    """

    project_id: int
    category: str | None
    budget_estimate: float = 0.0
    paper_bid_amount: float
    paper_bid_rate: float

    @classmethod
    def from_candidate(
        cls, item: PaperBiddingCandidateItem
    ) -> "PaperBiddingSettlementInput":
        """후보 산출을 정산 입력으로 좁힌다(historical replay 경로).

        forward 경로의 대응 어댑터는
        ``_ForwardSettlementMixin._paper_bid_to_settlement_input`` 이다 — 둘 다 이
        모델로 수렴하므로 정산 수식은 하나만 존재한다.
        """
        return cls(
            project_id=item.project_id,
            category=item.category,
            budget_estimate=item.budget_estimate,
            paper_bid_amount=item.paper_bid_amount,
            paper_bid_rate=item.paper_bid_rate,
        )


class PaperBiddingSettlementItem(StrictModel):
    """페이퍼 투찰 1건을 실낙찰과 낙찰하한 게이트에 대조한 정산 결과.

    필드 순서는 종전 ``_build_settlement_item`` dict 리터럴 순서를 따른다.
    """

    project_id: int
    # Breakdown 키(Phase 2 Experiment Lab): 카테고리/예산밴드 집계를 프로젝트
    # 재조회 없이 하려고 후보에서 그대로 넘겨받는다.
    category: str | None
    budget_estimate: float
    # 개찰/안내 시각(isoformat). 카테고리별 데이터 신선도 노출에 쓴다.
    result_time: str
    tender_result_id: int
    result_status: str
    winning_company: str | None
    winning_amount: float
    winning_rate: float
    amount_delta: float
    absolute_error_rate: float
    bid_rate_delta: float
    absolute_bid_rate_error: float
    price_close: bool
    price_competitive: bool
    would_have_won_price_only: WouldHaveWonPriceOnly
    would_have_won_final: WouldHaveWonFinal
    # 예정가/낙찰하한가를 유도할 수 없으면 None 이고, 게이트는 "unknown" 이 된다.
    estimated_price: float | None
    minimum_bid_price: float | None
    settlement_reason: str


class PaperBiddingRunSummary(StrictModel):
    """run 1회의 후보/정산 롤업.

    모든 필드에 기본값이 있는 유일한 산출 DTO다. 이 모델은 ``PaperBidRun.result_payload``
    로 영속화되고 과거 실행이 남긴 payload 도 되읽어야 하는데(지표가 시간에 따라
    추가됐다), 키가 하나 없다고 대시보드가 죽으면 안 되기 때문이다. 생산 경로의
    누락은 기본값에 가려지지 않도록 ``model_fields_set`` 계약 테스트로 잡는다.

    평균 오차는 정산 표본이 없으면 ``None`` 이다(0.0 으로 적으면 "오차 0" 으로
    읽히므로 정직 명세상 부재를 유지한다).
    """

    candidate_count: int = 0
    paper_bid_count: int = 0
    review_count: int = 0
    skip_count: int = 0
    skipped_by_strategy_count: int = 0
    skipped_invalid_count: int = 0
    settled_count: int = 0
    action_counts: dict[str, int] = Field(default_factory=dict)
    average_absolute_bid_rate_error: float | None = None
    average_absolute_amount_error_rate: float | None = None
    within_0_1pct_count: int = 0
    within_0_3pct_count: int = 0
    within_1pct_count: int = 0
    price_close_count: int = 0
    price_competitive_count: int = 0
    would_have_won_price_only_count: int = 0
    would_have_won_final_eligible_favorable_count: int = 0
    would_have_won_final_eligible_but_outbid_count: int = 0
    would_have_won_final_disqualified_count: int = 0
    would_have_won_final_unknown_count: int = 0


class PersistedPaperBiddingRunSummary(PaperBiddingRunSummary):
    """저장된 ``PaperBidRun.result_payload`` 복원용 — 과거 실행이 남긴 미지 키를 버린다.

    산출 DTO 는 ``extra="forbid"`` 여야 오타 키를 잡을 수 있지만, **되읽기**는
    다르다. 지표가 삭제·개명된 시절의 payload 를 forbid 로 읽으면 오래된 run 하나가
    목록 API 전체를 500 으로 만든다. 그래서 복원 전용 서브클래스만 미지 키를
    무시한다(생산 경로는 여전히 forbid).
    """

    model_config = ConfigDict(extra="ignore")
