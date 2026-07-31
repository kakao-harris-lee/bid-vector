"""Prediction accuracy, feedback, KPI and observability reporting schemas."""

from datetime import datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.core.constants import PaperBidAction


class PredictionFeedbackItem(BaseModel):
    project_id: int
    project_title: str
    category: Optional[str] = None
    tender_result_id: int
    result_status: str
    announced_at: Optional[datetime] = None
    winning_amount: float
    winning_rate: float
    latest_prediction_id: Optional[int] = None
    predicted_price: Optional[float] = None
    prediction_delta_amount: Optional[float] = None
    prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    latest_decision_record_id: Optional[int] = None
    recommended_amount: Optional[float] = None
    recommendation_delta_amount: Optional[float] = None
    recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    recommendation_improved_vs_prediction: Optional[bool] = None


class PredictionFeedbackResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    period_days: int
    result_count: int = Field(ge=0)
    prediction_sample_count: int = Field(ge=0)
    recommendation_sample_count: int = Field(ge=0)
    average_prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    prediction_within_1_percent_count: int = Field(ge=0)
    prediction_within_3_percent_count: int = Field(ge=0)
    recommendation_within_1_percent_count: int = Field(ge=0)
    recommendation_within_3_percent_count: int = Field(ge=0)
    recommendation_better_than_prediction_count: int = Field(ge=0)
    items: List[PredictionFeedbackItem] = Field(default_factory=list)


class AccuracyReportSummary(BaseModel):
    """Top-line accuracy of recommended price vs. actual winning price.

    error = |추천가 - 실제낙찰가| / 실제낙찰가. 정산 완료(실제 낙찰가 보유) 건만 집계하며,
    단일 운영자(canonical) 기준이다. ``within_*`` 는 추천가 오차가 해당 임계 이하인 표본 수/비율.
    """

    period_days: int = Field(ge=0)
    matched_sample_count: int = Field(
        ge=0,
        description="정산 완료·매칭된 비교 표본 수(집계 대상 건수). 상한(limit)에 막히면 truncated=True.",
    )
    truncated: bool = Field(
        default=False,
        description="상한(limit) 도달로 가장 최근 건만 집계돼 일부 과거 비교 건이 누락됐을 수 있음.",
    )
    limit: int = Field(ge=0, description="집계에 적용된 유효 상한(limit) 건수.")
    recommendation_sample_count: int = Field(ge=0)
    prediction_sample_count: int = Field(ge=0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    average_prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    recommendation_better_than_prediction_count: int = Field(ge=0)
    within_1pct_count: int = Field(ge=0)
    within_3pct_count: int = Field(ge=0)
    within_5pct_count: int = Field(ge=0)
    within_1pct_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    within_3pct_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    within_5pct_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AccuracyReportErrorBin(BaseModel):
    """One bucket of the recommendation error-rate distribution.

    ``lower``/``upper`` 는 오차율 경계(upper=None 이면 상한 없음). 표본은 ``lower < rate <= upper``
    규칙으로 배정한다(맨 아래 구간만 ``rate <= upper``).
    """

    bin_label: str
    lower: float = Field(ge=0.0)
    upper: Optional[float] = Field(default=None, ge=0.0)
    count: int = Field(ge=0)
    rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AccuracyReportCategory(BaseModel):
    """Per-category accuracy. ``category`` None 은 ``(미분류)`` 로 노출한다."""

    category: str
    sample_count: int = Field(ge=0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    within_3pct_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AccuracyReportTrendBucket(BaseModel):
    """Weekly accuracy trend bucketed by ``announced_at`` (chronological)."""

    period_start: datetime
    period_end: datetime
    sample_count: int = Field(ge=0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)


class AccuracyReportResponse(BaseModel):
    """Consolidated 추천 vs 실제 낙찰가 정확도 리포트.

    추천가(``recommended_amount``)와 실제 낙찰가(``winning_amount``)의 실측 비교다.
    정산 완료 건만 포함(미정산 제외) — would_have_won/price-only 추정 지표와 달리 실측이다.
    """

    operator_id: int
    current_operator_id: int
    current_operator_username: str
    summary: AccuracyReportSummary
    error_distribution: List[AccuracyReportErrorBin] = Field(default_factory=list)
    per_category: List[AccuracyReportCategory] = Field(default_factory=list)
    time_trend: List[AccuracyReportTrendBucket] = Field(default_factory=list)
    items: List[PredictionFeedbackItem] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class OperationsKpiManualOverride(BaseModel):
    """Manual-override KPI (d): how often the operator changed a recommendation."""

    decision_count: int = Field(ge=0)
    modified_count: int = Field(ge=0)
    modification_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class OperationsKpiConversion(BaseModel):
    """Conversion KPI (e): recommendation-to-submission rates reused from the funnel."""

    decision_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    overall_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_submission_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_hours_to_submit: Optional[float] = Field(default=None, ge=0.0)


class OperationsKpiPredictionAccuracy(BaseModel):
    """Prediction-accuracy KPI (f): error rates reused from prediction feedback."""

    result_count: int = Field(ge=0)
    prediction_sample_count: int = Field(ge=0)
    recommendation_sample_count: int = Field(ge=0)
    average_prediction_error_rate: Optional[float] = Field(default=None, ge=0.0)
    average_recommendation_error_rate: Optional[float] = Field(default=None, ge=0.0)
    prediction_within_1_percent_count: int = Field(ge=0)
    prediction_within_3_percent_count: int = Field(ge=0)
    recommendation_within_1_percent_count: int = Field(ge=0)
    recommendation_within_3_percent_count: int = Field(ge=0)


class OperationsKpiMissedOpportunityItem(BaseModel):
    """One recommended-but-unactioned tender whose deadline has already passed."""

    decision_record_id: int
    project_id: int
    project_title: str
    deadline: Optional[datetime] = None
    initial_action: str
    decision_status: str
    priority_score: float


class OperationsKpiMissedOpportunities(BaseModel):
    """Missed-opportunity KPI (b): bid_now/review recommendations left past deadline."""

    missed_count: int = Field(ge=0)
    items: List[OperationsKpiMissedOpportunityItem] = Field(default_factory=list)


class OperationsKpiReviewTime(BaseModel):
    """Review-time KPI (a): minutes between first viewing a tender and deciding."""

    average_review_minutes: Optional[float] = Field(default=None, ge=0.0)
    sample_count: int = Field(ge=0)


class OperationsKpiRecommendationFeedback(BaseModel):
    """Recommendation-usefulness KPI (c): operator 👍/👎 votes on recommendations."""

    useful_count: int = Field(ge=0)
    not_useful_count: int = Field(ge=0)
    review_value_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    feedback_count: int = Field(ge=0)


class OperationsKpiSettlementCoverage(BaseModel):
    """Settlement-coverage KPI: how far paper-bid settlement has progressed.

    ``forward_*`` fields isolate the ``forward_paper`` run subset, which is the
    cohort the automated forward-settlement job is responsible for closing.
    Coverage rates are ``None`` when their denominator (paper-bid count) is zero.
    """

    total_paper_bids: int = Field(ge=0)
    settled_count: int = Field(ge=0)
    coverage_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    forward_paper_bids: int = Field(ge=0)
    forward_settled_count: int = Field(ge=0)
    forward_coverage_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class OperationsKpiResponse(BaseModel):
    """Roadmap C-1 instrumentation: operating KPIs aggregated in one call."""

    operator_id: int
    current_operator_id: int
    current_operator_username: str
    period_days: int
    manual_override: OperationsKpiManualOverride
    conversion: OperationsKpiConversion
    prediction_accuracy: OperationsKpiPredictionAccuracy
    missed_opportunities: OperationsKpiMissedOpportunities
    review_time: OperationsKpiReviewTime
    recommendation_feedback: OperationsKpiRecommendationFeedback
    settlement_coverage: OperationsKpiSettlementCoverage


class RecommendationFeedbackLabelBreakdown(BaseModel):
    """Useful/not_useful split for a single category or action bucket."""

    useful: int = Field(ge=0)
    not_useful: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RecommendationFeedbackLabelItem(BaseModel):
    """One operator-verdict label joined with its decision/project context."""

    decision_record_id: int
    project_id: int
    project_title: str
    project_category: Optional[str] = None
    project_business_type_code: Optional[str] = None
    action: PaperBidAction
    decision_status: Literal["planned", "reviewing", "submitted", "skipped"]
    priority_score: float = Field(ge=0.0, le=1.0)
    verdict: Literal["useful", "not_useful"]
    feedback_at: Optional[datetime] = None
    reasoning: str = ""
    strengths: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)


class RecommendationFeedbackLabelsResponse(BaseModel):
    """Recommendation feedback labels exported for ML/QA review.

    Top-level counts mirror :class:`OperationsKpiRecommendationFeedback` so the
    KPI card and this label export stay numerically consistent. ``by_category``
    and ``by_action`` group verdicts by the joined project category and the
    decision's current action; ``items`` is the deduped (latest-per-decision)
    label rows, newest feedback first.
    """

    operator_id: int
    current_operator_id: int
    current_operator_username: str
    period_days: int
    label_count: int = Field(ge=0)
    useful_count: int = Field(ge=0)
    not_useful_count: int = Field(ge=0)
    review_value_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    by_category: Dict[str, RecommendationFeedbackLabelBreakdown] = Field(
        default_factory=dict
    )
    by_action: Dict[str, RecommendationFeedbackLabelBreakdown] = Field(
        default_factory=dict
    )
    items: List[RecommendationFeedbackLabelItem] = Field(default_factory=list)


class PredictionPredictorBreakdownItem(BaseModel):
    predictor_name: str
    predictor_family: str
    prediction_count: int = Field(ge=0)
    selection_rate: float = Field(ge=0.0, le=1.0)
    fallback_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_count: int = Field(ge=0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)
    accuracy_sample_count: int = Field(ge=0)
    average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    within_1_percent_count: int = Field(ge=0)
    within_3_percent_count: int = Field(ge=0)
    average_confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_training_window_size: Optional[float] = Field(default=None, ge=0.0)
    average_predicted_bid_rate: Optional[float] = Field(default=None, ge=0.0)


class PredictionPricingModeBreakdownItem(BaseModel):
    pricing_mode: str
    prediction_count: int = Field(ge=0)
    selection_rate: float = Field(ge=0.0, le=1.0)
    fallback_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_count: int = Field(ge=0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)


class PredictionPerformanceTrendItem(BaseModel):
    bucket_start: datetime
    bucket_end: datetime
    prediction_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)
    accuracy_sample_count: int = Field(ge=0)
    average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    backtest_sample_count: int = Field(ge=0)
    average_backtest_error_rate: Optional[float] = Field(default=None, ge=0.0)


class PredictionObservabilityResponse(BaseModel):
    operator_id: int
    current_operator_id: int
    current_operator_username: str
    period_days: int
    prediction_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    fallback_rate: float = Field(ge=0.0, le=1.0)
    guardrail_count: int = Field(ge=0)
    guardrail_rate: float = Field(ge=0.0, le=1.0)
    accuracy_sample_count: int = Field(ge=0)
    average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    within_1_percent_count: int = Field(ge=0)
    within_3_percent_count: int = Field(ge=0)
    predictor_breakdown: List[PredictionPredictorBreakdownItem] = Field(
        default_factory=list
    )
    pricing_mode_breakdown: List[PredictionPricingModeBreakdownItem] = Field(
        default_factory=list
    )
    performance_trend: List[PredictionPerformanceTrendItem] = Field(
        default_factory=list
    )
    fallback_reason_breakdown: dict[str, int] = Field(default_factory=dict)
    guardrail_reason_breakdown: dict[str, int] = Field(default_factory=dict)
