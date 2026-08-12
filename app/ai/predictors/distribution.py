"""예정가 분포 predictor — 복수예비가격 추첨(4/15) × 계층 수축 사전분포 (Phase 1).

예정가는 시계열 예측 대상이 아니라 복권형 생성 메커니즘이다(2026-08-09 운영자
도메인 판정, #360 LSTM 은퇴). 이 predictor 는 그 메커니즘을 그대로 모형화한다:

1. 이력 행마다 게시된 복수예비가격/기초금액 비율에서 추첨 평균의 (중심, 분산)을
   닫힌식으로 얻는다 — :mod:`app.domain.reserve_draw_distribution`.
2. 공고별 중심을 발주기관 → 공종 → 전역 3계층으로 수축한다(학습 코어 기관 1,837곳
   중 91.9% 가 표본 <10 이라 점추정 불가) — :mod:`app.domain.assessment_shrinkage`.
3. 다음 공고의 예정가/기초금액 예측 분포 = 수축된 중심 분포 + 공고 내 추첨 분산.
   투찰율은 이력의 낙찰율/실현 사정률 비 중앙값을 분포 중심·구간 경계에 곱해 얻는다.

정직 명세(§2): 산출은 가격 적합도 추정이며 실제 낙찰 확률이 아니다. guardrail 은
이 모듈이 아니라 기존 ``_apply_prediction_guardrails`` 단계가 그대로 적용한다 —
여기서는 어떤 하한도 선점하거나 우회하지 않는다. 통계 밴드 폴드(procurement band)도
적용하지 않는다: 분포 자체의 캘리브레이션을 검증 가능하게 남기는 것이 Phase 1 의
목적이고, 법정하한은 guardrail 단계가 보장한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import fmean, median

from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PredictorAvailability,
    PricePredictionContext,
)
from app.ai.predictors.historical import (
    normalize_agency_name,
    normalize_category_key,
    read_record_value,
    summarize_historical_records,
)
from app.ai.predictors.historical.statistics import resolve_record_bid_rate
from app.core.config import settings
from app.domain.assessment_shrinkage import (
    LEVEL_AGENCY,
    AssessmentPosterior,
    resolve_assessment_posterior,
)
from app.domain.reserve_draw_distribution import DEFAULT_DRAW_COUNT
from app.services.base_amount_basis import BASIS_CLEAN
from app.utils.sequence_coercion import coerce_integer_list, coerce_numeric_list

from app.ai.predictors.distribution_extraction import (
    aggregate_level_observation,
    observe_reserve_draw,
)
from app.ai.predictors.scenario_spec import build_scenario_candidates

_MODEL_VERSION = "v1.0-distribution"
_PRICING_MODE = "reserve_distribution"

# 신뢰도: 유효 표본 깊이 + 예측 분포 폭. 예측 std 0.02(사정률 2%p)에서 tightness 0.
_CONFIDENCE_BASE = 0.5
_CONFIDENCE_SAMPLE_WEIGHT = 0.3
_CONFIDENCE_SAMPLE_SATURATION = 24.0
_CONFIDENCE_TIGHTNESS_WEIGHT = 0.15
_CONFIDENCE_STD_NORMALIZER = 0.02
_CONFIDENCE_MIN = 0.45
_CONFIDENCE_MAX = 0.95


@dataclass(frozen=True)
class _ReserveObservation:
    """이력 한 행에서 추출한 추첨 분포 관측치(순수 값)."""

    center: float
    draw_variance: float
    agency_key: str
    category_key: str
    bid_to_assessment_ratio: float | None


@dataclass(frozen=True)
class _DistributionEstimate:
    """분포 추정의 중간 산물 — payload 조립과 통계 추정을 분리한다(§4.7)."""

    posterior: AssessmentPosterior
    predictive_std: float
    bid_ratio: float
    observation_count: int
    agency_observation_count: int
    ratio_sample_count: int


class ReserveDrawDistributionPredictor(BasePricePredictor):
    """복수예비가격 추첨 분포 + 계층 수축 사전분포 기반 predictor."""

    name = "reserve_draw_distribution"
    family = "distribution"

    def check_availability(self, context: PricePredictionContext) -> PredictorAvailability:
        """실험 플래그·기초금액·예비가격 관측 깊이·투찰율 환산 표본을 점검한다."""
        if not settings.PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS:
            return PredictorAvailability(False, "Experimental predictors are disabled.")
        if float(context.budget or 0.0) <= 0:
            return PredictorAvailability(
                False, "A positive budget is required for distribution inference."
            )
        observations = _extract_reserve_observations(context)
        min_records = int(settings.PRICE_PREDICTION_DISTRIBUTION_MIN_RESERVE_RECORDS)
        if len(observations) < min_records:
            return PredictorAvailability(
                False,
                f"At least {min_records} historical rows with usable reserve prices are "
                f"required for distribution inference (got {len(observations)}).",
            )
        ratio_samples = _ratio_samples(observations)
        min_ratios = int(settings.PRICE_PREDICTION_DISTRIBUTION_MIN_BID_RATIO_SAMPLES)
        if len(ratio_samples) < min_ratios:
            return PredictorAvailability(
                False,
                f"At least {min_ratios} historical rows with a realized 사정률 and bid "
                f"rate are required to convert the 예정가 distribution into a bid rate "
                f"(got {len(ratio_samples)}).",
            )
        return PredictorAvailability(True)

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """예정가 분포를 추정하고 기존 17필드 계약으로 검증해 반환한다."""
        return build_distribution_prediction(context)


def build_distribution_prediction(context: PricePredictionContext) -> PredictionResult:
    """분포 추정 → 시나리오 → 검증된 결과. 검증은 typed 생성자 단일 지점이다."""
    estimate = _estimate_distribution(context)
    budget = float(context.budget or 0.0)
    # 시나리오 후보 조립은 scenario_spec 한 벌이다(§4.5-8). 분포 중심·예측 표준편차는
    # 사정률 축이므로 낙찰율/실현 사정률 비를 ``scale`` 로 넘겨 투찰율 축으로 옮긴다.
    # 실측 커버리지가 명목 80% 보다 넓은 이유(첨도)는 그 모듈이 설명한다.
    candidates = build_scenario_candidates(
        center=estimate.posterior.mean,
        std=estimate.predictive_std,
        budget=budget,
        scale=estimate.bid_ratio,
    )
    base_candidate = candidates[1]
    historical_summary = summarize_historical_records(
        context.historical_records, agency_name=context.agency_name
    )
    return PredictionResult(
        predicted_price=float(base_candidate["predicted_price"]),
        price_range_min=min(candidate["predicted_price"] for candidate in candidates),
        price_range_max=max(candidate["predicted_price"] for candidate in candidates),
        confidence_score=_estimate_distribution_confidence(estimate),
        model_version=_MODEL_VERSION,
        pricing_mode=_PRICING_MODE,
        # 이 predictor 가 **실제로 소비한** 관측 수다 — summarize 의 전체 행 수를
        # 실으면 explanation 의 관측 수와 한 응답이 두 숫자를 보고하고, 그 값이
        # PricePrediction 으로 영속화돼 정확도 리포트까지 간다(리뷰 L4-2).
        historical_sample_size=estimate.observation_count,
        agency_match_sample_size=estimate.agency_observation_count,
        predicted_bid_rate=float(base_candidate["bid_rate"]),
        bid_rate_candidates=candidates,
        reserve_price_context=historical_summary.get("reserve_price_context"),
        feedback_calibration=None,
        guardrail_applied=False,
        guardrail_reason=None,
        floor_bid_rate=None,
        floor_price=None,
        competitive_target_bid_rate=float(base_candidate["bid_rate"]),
        explanation=_build_distribution_explanation(estimate),
    )


def _estimate_distribution(context: PricePredictionContext) -> _DistributionEstimate:
    """관측 추출 → 계층 수축 → 예측 분포 모수. 표본 부족은 크게 실패시킨다."""
    observations = _extract_reserve_observations(context)
    if not observations:
        raise ValueError("No historical rows with usable reserve prices were available.")
    ratio_samples = _ratio_samples(observations)
    if not ratio_samples:
        raise ValueError("No historical rows could link a realized 사정률 to a bid rate.")

    agency_key = normalize_agency_name(context.agency_name)
    posterior = _resolve_posterior(
        observations,
        agency_key=agency_key,
        category_key=normalize_category_key(context.category),
    )
    # 예측 분포 = 공고 중심의 수축 사후분포 + 공고 내 추첨 분산(메커니즘 고유 노이즈).
    within_draw_variance = fmean(
        observation.draw_variance for observation in observations
    )
    return _DistributionEstimate(
        posterior=posterior,
        predictive_std=sqrt((posterior.std**2) + within_draw_variance),
        bid_ratio=float(median(ratio_samples)),
        observation_count=len(observations),
        agency_observation_count=sum(
            1
            for observation in observations
            if agency_key and observation.agency_key == agency_key
        ),
        ratio_sample_count=len(ratio_samples),
    )


def _extract_reserve_observations(
    context: PricePredictionContext,
) -> list[_ReserveObservation]:
    """이력 행에서 추첨 분포 관측치를 추출한다.

    basis 는 **fail-closed** 다: ``clean`` 태그 행만 쓴다(#199 — models.py 가
    "clean 행만 소비"를 강제하고, 태그 없는 행을 통과시키면 분류기가 거절했을
    비정수 base 행이 바로 그 구멍으로 들어온다. 리뷰 L4-3). 행→관측 산술(정확히
    15개·center 개연 밴드·실현 사정률·환산 비)은 ``observe_reserve_draw`` 가 단일
    출처다 — 캘리 스크립트도 같은 함수를 쓴다(리뷰 L4-1).
    """
    observations: list[_ReserveObservation] = []
    for record in context.historical_records:
        basis = read_record_value(record, "base_amount_basis")
        if str(basis or "") != BASIS_CLEAN:
            continue
        observed = observe_reserve_draw(
            reserve_prices=coerce_numeric_list(
                read_record_value(record, "reserve_prices")
            ),
            base_amount=float(read_record_value(record, "base_amount") or 0.0),
            picked_numbers=coerce_integer_list(
                read_record_value(record, "selected_numbers")
            ),
            bid_rate=resolve_record_bid_rate(record),
        )
        if observed is None:
            continue
        observations.append(
            _ReserveObservation(
                center=observed.center,
                draw_variance=observed.draw_variance,
                agency_key=normalize_agency_name(read_record_value(record, "agency_name")),
                category_key=normalize_category_key(read_record_value(record, "category")),
                bid_to_assessment_ratio=observed.bid_to_assessment_ratio,
            )
        )
    return observations


def _ratio_samples(observations: list[_ReserveObservation]) -> list[float]:
    return [
        observation.bid_to_assessment_ratio
        for observation in observations
        if observation.bid_to_assessment_ratio is not None
    ]


def _resolve_posterior(
    observations: list[_ReserveObservation],
    *,
    agency_key: str,
    category_key: str,
) -> AssessmentPosterior:
    """공고의 기관·공종 키로 계층 관측을 집계해 수축 사후분포를 얻는다."""
    global_observation = aggregate_level_observation(
        [observation.center for observation in observations]
    )
    if global_observation is None:
        raise ValueError("No reserve observations were available for the posterior.")
    return resolve_assessment_posterior(
        agency=aggregate_level_observation(
            [
                observation.center
                for observation in observations
                if agency_key and observation.agency_key == agency_key
            ]
        ),
        category=aggregate_level_observation(
            [
                observation.center
                for observation in observations
                if category_key and observation.category_key == category_key
            ]
        ),
        global_level=global_observation,
    )


def _estimate_distribution_confidence(estimate: _DistributionEstimate) -> float:
    """유효 표본 깊이와 예측 분포 폭에서 신뢰도를 추정한다(선언 상수 §4.5)."""
    sample_score = min(
        estimate.posterior.effective_sample_count / _CONFIDENCE_SAMPLE_SATURATION, 1.0
    )
    tightness_score = max(
        0.0, 1.0 - min(estimate.predictive_std / _CONFIDENCE_STD_NORMALIZER, 1.0)
    )
    confidence = (
        _CONFIDENCE_BASE
        + (sample_score * _CONFIDENCE_SAMPLE_WEIGHT)
        + (tightness_score * _CONFIDENCE_TIGHTNESS_WEIGHT)
    )
    return round(max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, confidence)), 2)


def _build_distribution_explanation(estimate: _DistributionEstimate) -> str:
    """정직 명세(§2)에 맞는 설명 — 분포 추정이지 낙찰 확률 단정이 아니다."""
    posterior = estimate.posterior
    agency_weight = float(posterior.level_weights.get(LEVEL_AGENCY, 0.0))
    return (
        f"복수예비가격 {DEFAULT_DRAW_COUNT}개 추첨 평균 분포(이력 "
        f"{estimate.observation_count}건)와 발주기관→공종→전역 계층 수축 사전분포로 "
        f"예정가 분포를 추정했습니다. 사정률 중심 {posterior.mean:.4f}"
        f"±{estimate.predictive_std:.4f}, 유효 표본 "
        f"{posterior.effective_sample_count:.1f}건(기관 반영 비중 {agency_weight:.0%}). "
        f"투찰율은 과거 낙찰율/실현 사정률 비 중앙값 {estimate.bid_ratio:.4f}"
        f"({estimate.ratio_sample_count}건)를 분포 중심에 곱해 산출했고, "
        f"보수/공격 시나리오는 정규근사 ±1.28σ 경계입니다."
    )
