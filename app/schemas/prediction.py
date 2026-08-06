"""ML task polling and price-prediction request-response schemas."""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from app.core.constants import PriceScenario
from app.schemas._base import StrictModel


class MLTaskResponse(BaseModel):
    task_id: str
    task_name: str
    queue: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    detail: str
    poll_url: str


class MLTaskStatusResponse(BaseModel):
    task_id: str
    task_name: str
    queue: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    raw_status: str
    ready: bool
    successful: bool
    detail: str
    error: Optional[str] = None
    result: Optional[dict] = None


class PricePredictionTrainingRequest(BaseModel):
    release_tag: Optional[str] = None
    category: Optional[str] = None
    agency_name: Optional[str] = None
    limit: int = Field(default=500, ge=1, le=5000)
    notes: Optional[str] = None
    create_manifest: bool = True
    publish_remote: bool = True


class PricePredictionRequest(BaseModel):
    project_id: int
    budget_estimate: float
    category: str
    description: str
    agency_name: Optional[str] = None
    legal_floor_bid_rate: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="공고별 법정 낙찰하한율. 0.87995 또는 87.995 모두 허용.",
    )


class PricePredictionScenario(BaseModel):
    label: PriceScenario
    bid_rate: float
    predicted_price: float
    confidence_weight: float = Field(ge=0.0, le=1.0)
    guardrail_applied: bool = False
    pre_guardrail_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    pre_guardrail_price: Optional[float] = Field(default=None, ge=0.0)
    price_granularity_applied: bool = False
    pre_granularity_price: Optional[float] = Field(default=None, ge=0.0)


class BidTargetOption(BaseModel):
    label: Literal["recommended", "aggressive", "safe"]
    stance: str
    bid_rate: float = Field(ge=0.0)
    bid_price: Optional[float] = Field(default=None, ge=0.0)
    risk_note: str
    basis: str


class BidTargetMenu(BaseModel):
    options: List[BidTargetOption]
    band_floor_rate: Optional[float] = Field(default=None, ge=0.0)
    band_ceiling_rate: Optional[float] = Field(default=None, ge=0.0)
    signals_summary: str
    caveat: str
    collapsed: bool = False


class PricePredictionReservePattern(BaseModel):
    sample_count: int = Field(ge=0)
    average_reserve_span_rate: float = Field(ge=0.0)
    estimated_price_sample_count: int = Field(default=0, ge=0)
    average_estimated_price_rate: float = Field(default=0.0, ge=0.0)
    median_estimated_price_rate: float = Field(default=0.0, ge=0.0)
    median_bid_to_estimated_price_rate: float = Field(default=0.0, ge=0.0)
    average_selected_number: float = Field(ge=0.0)
    frequent_selected_numbers: List[int] = Field(default_factory=list)


class PricePredictionFeedbackCalibration(BaseModel):
    sample_count: int = Field(ge=0)
    agency_match_sample_count: int = Field(ge=0)
    average_signed_error_rate: float
    average_absolute_error_rate: float = Field(ge=0.0)
    applied_adjustment_rate: float


class FloorShortfallEstimate(StrictModel):
    """추천 투찰가가 낙찰하한 미달이 됐을 **과거 빈도**(추정) — 실격 확률이 아니다.

    추천가는 기초금액 기준이고 실격 하한은 추첨된 예정가격 기준이라, 사정률
    (예정가/기초금액) 추첨 결과에 따라 같은 추천가도 하한 위/아래로 갈린다. 이 DTO 는
    과거 개찰 표본에서 그 경계를 넘긴 **표본 비율**만 전달한다(정직 명세 §2). 이번
    공고의 추첨에 대한 확률 주장이 아니므로 UI/문구에서 "실격 확률"로 표시하면 안 된다.

    ``shortfall_frequency is None`` 은 **"위험 없음"이 아니라 "판정 불가"** 다
    (``unmeasurable_reason`` 참조). 0.0 과 절대 같은 표시로 합치지 않는다.
    """

    shortfall_frequency: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "과거 사정률 표본 중 이 추천율이 낙찰하한 미달이 됐을 표본 비율(0-1). "
            "실제 실격 확률이 아니다. None 이면 판정 불가(표본 부족 등)."
        ),
    )
    shortfall_sample_count: int = Field(
        default=0,
        ge=0,
        description="임계 사정률을 초과한 표본 수(빈도의 분자).",
    )
    sample_count: int = Field(
        default=0,
        ge=0,
        description="빈도 산출에 실제로 쓰인 사정률 표본 수(분모).",
    )
    minimum_sample_count: int = Field(
        ge=0,
        description="빈도를 발표하기 위해 요구한 최소 표본 수(이 미만이면 판정 불가).",
    )
    critical_assessment_rate: Optional[float] = Field(
        default=None,
        gt=0.0,
        description=(
            "임계 사정률 = 추천 투찰율 ÷ 낙찰하한율. 사정률(예정가/기초금액)이 이 값을 "
            "초과하면 추천가가 하한 미달이 된다."
        ),
    )
    scope: str = Field(
        description="표본을 고른 기준(오염 필터·카테고리·기준일)을 사람이 읽을 수 있게 요약."
    )
    unmeasurable_reason: Optional[str] = Field(
        default=None,
        description="판정 불가 사유. 측정된 경우 None.",
    )


class PriceRegimeFeatures(BaseModel):
    buyer_sector: Optional[str] = None
    buyer_type: Optional[str] = None
    notice_category: Optional[str] = None
    business_type_code: Optional[str] = None
    business_group: Optional[str] = None
    construction_or_service_type: Optional[str] = None
    contract_method: Optional[str] = None
    award_method: Optional[str] = None
    evaluation_method: Optional[str] = None
    price_submission_mode: Optional[str] = None
    denominator_type: Optional[str] = None
    legal_floor_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    reserve_price_context_available: bool = False
    amount_bucket: Optional[str] = None
    agency_recent_rate_profile: dict = Field(default_factory=dict)
    data_quality_flags: List[str] = Field(default_factory=list)
    procurement_rate_band: Optional[str] = None
    price_regime_label: Optional[str] = None
    price_regime_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_required: bool = False
    regime_signals: List[str] = Field(default_factory=list)


class PricePredictionResponse(BaseModel):
    predicted_price: float
    price_range_min: float
    price_range_max: float
    confidence_score: float
    model_version: str
    predictor_name: str = "historical_statistical"
    predictor_family: str = "statistical"
    fallback_reason: Optional[str] = None
    selector_name: str = "configured_preference"
    selection_reason: Optional[str] = None
    backtest_sample_count: int = Field(default=0, ge=0)
    backtest_average_absolute_error_rate: Optional[float] = Field(default=None, ge=0.0)
    backtest_report: Optional[dict] = None
    training_window_size: int = Field(default=0, ge=0)
    pricing_mode: Literal["historical_blend", "heuristic"] = "heuristic"
    historical_sample_size: int = Field(default=0, ge=0)
    agency_match_sample_size: int = Field(default=0, ge=0)
    predicted_bid_rate: float = 0.0
    bid_base: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "투찰율을 곱한 기준금액(기초금액/사업금액, 과세 공고면 부가세 포함). "
            "predicted_price ≈ predicted_bid_rate × bid_base."
        ),
    )
    bid_base_source: Optional[str] = Field(
        default=None,
        description=(
            "위 기준금액의 출처. 수집된 기초금액이면 ReliableBaseSource 값, 공고의 "
            "추정가격으로 대체했으면 budget-estimate-fallback, 저장된 금액이 없어 요청 "
            "본문의 금액을 그대로 썼으면 client-budget-estimate(검증되지 않은 값)."
        ),
    )
    competitive_target_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    procurement_rate_band: Optional[str] = None
    bid_rate_candidates: List[PricePredictionScenario] = Field(default_factory=list)
    bid_target_menu: Optional[BidTargetMenu] = None
    reserve_price_context: Optional[PricePredictionReservePattern] = None
    feedback_calibration: Optional[PricePredictionFeedbackCalibration] = None
    guardrail_applied: bool = False
    guardrail_reason: Optional[str] = None
    legal_floor_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    floor_guardrail_source: Optional[str] = None
    floor_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    floor_price: Optional[float] = Field(default=None, ge=0.0)
    floor_safety_margin_rate: Optional[float] = Field(default=None, ge=0.0)
    safe_floor_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    safe_floor_price: Optional[float] = Field(default=None, ge=0.0)
    ceiling_bid_rate: Optional[float] = Field(default=None, ge=0.0)
    ceiling_price: Optional[float] = Field(default=None, ge=0.0)
    bid_price_granularity: Optional[int] = Field(default=None, ge=1)
    bid_price_rounding_mode: Optional[str] = None
    price_granularity_applied: bool = False
    price_regime_features: PriceRegimeFeatures = Field(default_factory=PriceRegimeFeatures)
    price_regime_label: Optional[str] = None
    price_regime_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_required: bool = False
    recommended_candidate_label: Optional[str] = None
    recommended_selector_reason: Optional[str] = None
    explanation: str = ""
