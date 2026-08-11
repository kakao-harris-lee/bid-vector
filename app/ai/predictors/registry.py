"""Predictor registry construction."""

from __future__ import annotations

from collections.abc import Mapping

from app.ai.predictors.base import BasePricePredictor
from app.ai.predictors.distribution import ReserveDrawDistributionPredictor
from app.ai.predictors.ensemble import EnsembleBidRatePredictor
from app.ai.predictors.historical import HistoricalStatisticalPredictor


def build_default_predictor_registry() -> dict[str, BasePricePredictor]:
    """Build the default in-process predictor registry.

    ``lstm`` 은 2026-08-09 은퇴했다. 은퇴한 키를 선호 설정이 계속 가리킬 수 있으므로,
    그 폴백 사유는 ``price_prediction.orchestration._RETIRED_PREDICTOR_KEYS`` 가 선언한다.
    ``distribution`` 은 Phase 1 예정가 분포 엔진(추첨 열거 + 계층 수축)이다. 라이브
    게이트는 ``.env`` 선호 설정 **하나**다 — 실험 플래그는 이 배포에서 이미 true 라
    게이트가 아니다(2026-08-11 실측). 선호를 자동으로 바꾸는 두 경로(auto 선택의
    best 후보, manifest recommended_env)는 ``AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS``
    (app/core/constants.py)가 차단한다 — Phase 2 게이트 통과 전까지 이 키는 명시
    선호로만 실행된다.
    """
    return {
        "historical": HistoricalStatisticalPredictor(),
        "ensemble": EnsembleBidRatePredictor(),
        "distribution": ReserveDrawDistributionPredictor(),
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
