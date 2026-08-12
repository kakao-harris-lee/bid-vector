"""Predictor registry construction."""

from __future__ import annotations

from collections.abc import Mapping

from app.ai.predictors.award_rate_gbm import AwardRateGbmPredictor
from app.ai.predictors.base import BasePricePredictor
from app.ai.predictors.distribution import ReserveDrawDistributionPredictor
from app.ai.predictors.ensemble import EnsembleBidRatePredictor
from app.ai.predictors.historical import HistoricalStatisticalPredictor


def build_default_predictor_registry() -> dict[str, BasePricePredictor]:
    """Build the default in-process predictor registry.

    ``lstm`` 은 2026-08-09 은퇴했다. 은퇴한 키를 선호 설정이 계속 가리킬 수 있으므로,
    그 폴백 사유는 ``price_prediction.orchestration._RETIRED_PREDICTOR_KEYS`` 가 선언한다.
    ``distribution`` 은 Phase 1 예정가 분포 엔진(추첨 열거 + 계층 수축)이고,
    ``award_rate_gbm`` 은 Phase 2 낙찰률 GBM(공종 × 금액대 × 발주기관)이다.

    두 실험 predictor 의 라이브 게이트는 ``.env`` 선호 설정이다 — 실험 플래그는 이
    배포에서 이미 true 라 게이트가 아니다(2026-08-11 실측). GBM 은 아티팩트 경로
    (``PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH``, 기본 빈 문자열)라는 게이트가 하나
    더 있어, 선호만 켜고 학습 산출물이 없으면 unavailable 로 떨어져 historical 로
    폴백한다. 선호를 **자동으로** 바꾸는 세 경로(auto 선택의 best 후보, manifest
    recommended_env, 승격 게이트의 best arm)는 ``AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS``
    (app/core/constants.py)가 차단한다 — 승격 게이트 통과 전까지 두 키는 명시 선호로만
    실행된다.
    """
    return {
        "historical": HistoricalStatisticalPredictor(),
        "ensemble": EnsembleBidRatePredictor(),
        "distribution": ReserveDrawDistributionPredictor(),
        "award_rate_gbm": AwardRateGbmPredictor(),
    }


def normalize_predictor_registry(
    registry: Mapping[str, BasePricePredictor] | None,
) -> dict[str, BasePricePredictor]:
    """Return a mutable predictor registry with the historical fallback present."""
    if registry is None:
        resolved = build_default_predictor_registry()
    else:
        resolved = dict(registry)
    if "historical" not in resolved:
        resolved["historical"] = HistoricalStatisticalPredictor()
    return resolved
