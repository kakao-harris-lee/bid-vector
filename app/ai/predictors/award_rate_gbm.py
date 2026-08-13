"""낙찰률 GBM predictor — 공종 × 금액대 × 발주기관 표 형태 부스팅 (Phase 2).

무엇을 예측하는가
-----------------
타깃은 **basis 명시 낙찰률**(낙찰가 ÷ 신뢰 기초금액)이다. 사정률이 아니다: 사정률
분산의 대부분은 투찰 시점에 관측 불가(열린 공고는 예비가격 미공개)이고 나머지는 4/15
추첨 난수라 학습할 신호가 없다는 것을 Phase 1 이 못 박았다.

피처는 :class:`~app.domain.award_rate_features.AwardRateFeatureSpace` 가 조립한다 —
학습기(:mod:`app.services.ml_training.award_rate_gbm`)와 **같은 함수**다. 그래서 이
predictor 는 서빙 시점에 존재하는 것만 넘길 수 있다: 기초금액(``context.budget``) ·
공종 · 발주기관. 대상 공고의 예비가격 파생값은 시그니처상 넘길 자리가 없다.

분모 출처는 :data:`~app.domain.award_rate_features.SERVING_DENOMINATOR_SOURCE`
(``clean-base``)로 **고정**한다. 라이브 공고의 기초금액은 수집된 관측값이지 복수예비가격
midpoint 로 복구한 추정치가 아니므로, 복구 추정 층의 수준으로 예측하면 축이 어긋난다.

정직 명세(§2): 산출은 가격 적합도 추정이며 실제 낙찰 확률이 아니다. guardrail 은 이
모듈이 아니라 기존 ``_apply_prediction_guardrails`` 단계가 그대로 적용한다 — 여기서는
어떤 하한도 선점하거나 우회하지 않고, 통계 밴드 폴드도 하지 않는다(모델 자체의
캘리브레이션을 검증 가능하게 남기는 것이 이 Phase 의 목적이고, 법정하한은 guardrail
단계가 보장한다).

배우지 않은 공종에는 답하지 않는다
-----------------------------------
어휘에 없는 공종은 예외를 내지 않는다 — ``build_row`` 가 미지 코드를 내고 LightGBM 은
그것을 결측으로 다뤄, 모델이 **다른 공종의 행으로 학습한 잎**을 태워 그럴듯한 투찰율을
낸다. 배운 적 없는 공종에 자신 있게 답하는 셈이라 정직 명세(§2) 위반이고 가격 사고다.
그래서 학습 행 수가 임계 미만인 공종은 unavailable 로 떨어뜨려 historical 로 폴백한다
(:func:`unlearned_category_reason` — 판정 단일 지점). 실측 동기: 서빙 분포 정합 필터가
goods 를 학습 코퍼스에서 전량 제거했고(0행), general 은 6행뿐이다.

라이브 노출 게이트는 셋이다: 실험 플래그 · ``.env`` 선호 설정 · 아티팩트 경로. 셋 다
기본값이 "꺼짐"이고, 선호를 **자동으로** 바꾸는 경로(auto 선택의 best 후보, manifest
recommended_env, 승격 게이트의 best arm)는 ``AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS``
(app/core/constants.py)가 차단한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.ai.predictors.artifact_contracts import (
    ArtifactSource,
    PersistedAwardRateGbmArtifact,
    VersionAwareArtifactProvider,
    read_persisted_artifact,
)
from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PredictorAvailability,
    PricePredictionContext,
)
from app.ai.predictors.historical import clamp_bid_rate
from app.ai.predictors.scenario_spec import build_scenario_candidates
from app.core.config import settings
from app.domain.award_rate_features import (
    AWARD_RATE_FEATURE_NAMES,
    SERVING_DENOMINATOR_SOURCE,
    AgencyTargetEncoding,
    AwardRateFeatureSpace,
    normalize_feature_key,
)

if TYPE_CHECKING:  # pragma: no cover - lightgbm 은 런타임 의존성이 아니다(경계 유지)
    import lightgbm as lgb

__all__ = [
    "AwardRateGbmPredictor",
    "LoadedAwardRateGbmModel",
    "load_award_rate_gbm_model",
    "unlearned_category_reason",
]

_ARTIFACT_LABEL = "Award-rate GBM model artifact"
_PRICING_MODE = "award_rate_gbm"

# 신뢰도: 기관 표본 깊이 + 잔차 폭. 분포 엔진(distribution.py)과 같은 형태이되 축이
# 다르므로 정규화 상수는 이 축의 값이다 — 낙찰률 잔차 sd 는 5%p 규모라 그 자리에
# 사정률 축의 0.02 를 쓰면 tightness 가 항상 0 으로 포화한다.
_CONFIDENCE_BASE = 0.5
_CONFIDENCE_SAMPLE_WEIGHT = 0.3
_CONFIDENCE_SAMPLE_SATURATION = 24.0
_CONFIDENCE_TIGHTNESS_WEIGHT = 0.15
_CONFIDENCE_RESIDUAL_NORMALIZER = 0.06
_CONFIDENCE_MIN = 0.45
_CONFIDENCE_MAX = 0.95


@dataclass(frozen=True)
class LoadedAwardRateGbmModel:
    """복원된 아티팩트 — 피처 공간 + 부스터 + 잔차 폭.

    ``booster`` 의 타입 표기가 문자열인 것은 lightgbm 이 **런타임 의존성이 아니기
    때문**이다(``requirements/ml-training.txt`` 전용): 모듈 최상단에서 import 하면
    lightgbm 이 없는 프로세스에서 이 모듈 자체가 import 되지 않고, 그러면 레지스트리
    구성이 통째로 깨진다.

    이 값은 캐시에서 **공유**된다(``detach_copies=False``). 부스터의 deepcopy 는 모델
    텍스트를 통째로 재파싱하는 일이라 캐시 히트가 미스와 같은 비용이 되어 캐시가 무의미해진다.
    공유는 값이 불변일 때만 안전하므로 그 조건을 여기서 못 박는다:

    * 이 dataclass 와 :class:`~app.domain.award_rate_features.AwardRateFeatureSpace` ·
      :class:`~app.domain.award_rate_features.AgencyTargetEncoding` 이 모두
      ``frozen=True`` 다.
    * 어휘(``categories``·``denominator_sources``)는 tuple 이고, 인코딩 표 두 개는
      ``AgencyTargetEncoding.__post_init__`` 이 사본을 ``MappingProxyType`` 으로 봉인한다
      — 호출부가 반환된 표를 통해 캐시를 바꿀 수 있는 표면이 없다.
    * ``predict_rates`` 는 호출마다 새 행렬·새 리스트를 만들고 내부 상태를 남기지 않는다.
    * ``booster`` 는 **읽기 전용**으로만 쓴다(``predict``). lightgbm 4.7 의
      ``Booster.predict`` 는 호출마다 ``_InnerPredictor`` 를 새로 만들고
      (``manage_handle=False``) 부스터의 파이썬 상태를 바꾸지 않는다.
    """

    feature_space: AwardRateFeatureSpace
    booster: "lgb.Booster"
    residual_std: float
    model_version: str
    training_row_count: int

    def predict_rates(
        self, rows: list[tuple[float, str | None, str | None]]
    ) -> list[float]:
        """(금액, 공종, 기관) 목록의 낙찰률 예측 — 배치 경로(홀드아웃 평가가 쓴다).

        서빙 단건도 이 함수를 지나므로 배치와 단건이 갈리지 않는다.
        """
        if not rows:
            return []
        matrix = [
            self.feature_space.build_row(
                amount=amount,
                category=category,
                agency=agency,
                denominator_source=SERVING_DENOMINATOR_SOURCE.value,
            )
            for amount, category, agency in rows
        ]
        return [float(value) for value in self.booster.predict(matrix)]

    def agency_sample_count(self, *, agency: str | None, category: str | None) -> int:
        """이 공고의 (기관, 공종)을 뒷받침한 학습 표본 수 — 0 이면 미관측 기관이다."""
        _encoded, count = self.feature_space.agency_encoding.encode(
            agency=normalize_feature_key(agency),
            category=normalize_feature_key(category),
        )
        return count

    def category_training_rows(self, category: str | None) -> int:
        """이 공종을 배운 학습 행 수(도메인 커널 위임) — 공종 가드의 단일 근거."""
        return self.feature_space.category_training_rows(category)


def unlearned_category_reason(
    model: LoadedAwardRateGbmModel, category: str | None
) -> str | None:
    """이 공종으로 예측하면 안 되는 사유(없으면 ``None``) — **판정 단일 지점**.

    가용성 게이트(:meth:`AwardRateGbmPredictor.check_availability`)와 예측 경로
    (:func:`build_award_rate_gbm_prediction`)가 같은 이 함수를 부른다. 두 곳에 규칙을
    복사하면 임계가 갈릴 수 있고, 한쪽만 고치면 다른 쪽이 조용히 열린다.

    문구는 두 상태를 구분한다 — "배운 적 없는 공종"과 "표본이 얕은 공종"은 운영자에게
    다른 사실이고(전자는 수집·필터 문제, 후자는 시간 문제), 폴백 사유가 그것을 뭉개면
    무엇을 고쳐야 하는지 알 수 없다. 판정 규칙 자체는 한 수(학습 행 수) 하나다.
    """
    # 1 로 클램프한다: 설정을 0(또는 음수)으로 두면 ``rows >= minimum`` 이 항상 참이 되어
    # **어휘에 아예 없는 공종(0행)까지 통과**한다. 이 가드는 모듈 docstring 이 정직 명세
    # (§2) 보호로 선언한 것이고, 다른 red line(법정하한)이 env 한 값으로 꺼지지 않는 것과
    # 같은 대우여야 한다. 임계를 낮추는 것은 운영 재량이되 **끄는 것은 재량이 아니다.**
    minimum = max(1, int(settings.PRICE_PREDICTION_AWARD_RATE_GBM_MIN_CATEGORY_ROWS))
    rows = model.category_training_rows(category)
    if rows >= minimum:
        return None
    label = str(category or "").strip() or "(unset)"
    if rows <= 0:
        return (
            f"Award-rate GBM was never trained on category {label!r}; "
            "the model would answer it from other categories' rows."
        )
    return (
        f"Award-rate GBM has only {rows} training row(s) for category {label!r} "
        f"(minimum {minimum}); that is below one LightGBM leaf, so the category "
        "was not actually learned."
    )


def _load_award_rate_gbm_model_uncached(
    model_source: ArtifactSource,
) -> LoadedAwardRateGbmModel:
    """저장 아티팩트를 복원한다. 계약 위반·피처 불일치는 ``ValueError`` 로 나간다."""
    import lightgbm as lgb

    artifact = read_persisted_artifact(
        model_source, model=PersistedAwardRateGbmArtifact, label=_ARTIFACT_LABEL
    )
    if tuple(artifact.feature_names) != AWARD_RATE_FEATURE_NAMES:
        # 다른 피처 공간으로 학습된 부스터를 태우면 예외 없이 좌표만 어긋난다 —
        # 조용한 오답보다 unavailable 이 낫다(fail-closed).
        raise ValueError(
            f"{_ARTIFACT_LABEL} was trained on a different feature set "
            f"({artifact.feature_names!r} != {list(AWARD_RATE_FEATURE_NAMES)!r})"
        )
    encoding = AgencyTargetEncoding(
        agency_means={
            (entry.agency, entry.category): (entry.mean, entry.count)
            for entry in artifact.agency_encoding
        },
        category_means=dict(artifact.category_means),
        global_mean=artifact.global_mean,
    )
    return LoadedAwardRateGbmModel(
        feature_space=AwardRateFeatureSpace(
            categories=tuple(artifact.categories),
            denominator_sources=tuple(artifact.denominator_sources),
            agency_encoding=encoding,
        ),
        booster=lgb.Booster(model_str=artifact.booster_model),
        residual_std=float(artifact.residual_std),
        model_version=str(artifact.model_version),
        training_row_count=int(artifact.training_row_count),
    )


# 캐시된 모델을 **공유**한다: 부스터 deepcopy 는 모델 텍스트 재파싱이라 detach 를 켜면
# 캐시가 아무것도 아끼지 못한다. 공유가 안전한 근거(불변성)는 LoadedAwardRateGbmModel 참조.
_ARTIFACT_PROVIDER: VersionAwareArtifactProvider[LoadedAwardRateGbmModel] = (
    VersionAwareArtifactProvider(
        _load_award_rate_gbm_model_uncached, detach_copies=False
    )
)


def load_award_rate_gbm_model(
    model_source: ArtifactSource,
) -> LoadedAwardRateGbmModel:
    """버전 인지 캐시를 거쳐 아티팩트를 복원한다(파일이 바뀌면 자동 무효화)."""
    return _ARTIFACT_PROVIDER.load(model_source)


class AwardRateGbmPredictor(BasePricePredictor):
    """LightGBM 낙찰률 predictor (레지스트리 키 ``award_rate_gbm``)."""

    name = "award_rate_gbm"
    family = "gbm"

    def __init__(
        self,
        *,
        artifact_provider: VersionAwareArtifactProvider[LoadedAwardRateGbmModel]
        | None = None,
    ) -> None:
        self._artifact_provider = artifact_provider or _ARTIFACT_PROVIDER

    def check_availability(self, context: PricePredictionContext) -> PredictorAvailability:
        """실험 플래그 · 기초금액 · lightgbm 설치 · 아티팩트 · **공종**을 순서대로 점검한다.

        공종 점검이 마지막인 것은 아티팩트를 복원해야 어휘를 볼 수 있기 때문이다.
        """
        if not settings.PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS:
            return PredictorAvailability(False, "Experimental predictors are disabled.")
        if float(context.budget or 0.0) <= 0:
            return PredictorAvailability(
                False, "A positive budget is required for award-rate GBM inference."
            )
        model_path = str(
            settings.PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH or ""
        ).strip()
        if not model_path:
            return PredictorAvailability(
                False, "No award-rate GBM model artifact is configured."
            )
        if not Path(model_path).exists():
            return PredictorAvailability(
                False,
                f"Configured award-rate GBM model artifact was not found: {model_path}",
            )
        try:
            model = self._artifact_provider.load(model_path)
        except Exception as exc:
            # lightgbm 미설치(ImportError)도 여기로 떨어진다 — 런타임 이미지에는 그
            # 패키지가 없으므로 "설치되지 않음"은 장애가 아니라 정상적인 unavailable 이다.
            return PredictorAvailability(
                False, f"Award-rate GBM model artifact is unusable: {exc}"
            )
        unlearned = unlearned_category_reason(model, context.category)
        if unlearned is not None:
            return PredictorAvailability(False, unlearned)
        return PredictorAvailability(True)

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """아티팩트로 낙찰률을 추정하고 기존 17필드 계약으로 검증해 반환한다."""
        model_path = str(
            settings.PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH or ""
        ).strip()
        return build_award_rate_gbm_prediction(
            context, model=self._artifact_provider.load(model_path)
        )


def _guarded_center_rate(
    context: PricePredictionContext, model: LoadedAwardRateGbmModel
) -> float:
    """공종 가드를 통과한 뒤 중심 낙찰률 한 건을 낸다.

    가드가 가용성 게이트와 여기 둘 다 있는 것은 중복이 아니다 — 예측 경로는 게이트를 지나지
    않는 직접 호출부에도 열려 있고, 여기서 올린 예외는 orchestration 의 ``_run_predictor``
    가 받아 historical 로 복구하므로 실패 모드가 게이트와 같다(fail-closed). 판정 자체는
    :func:`unlearned_category_reason` 한 곳이라 두 경계의 임계가 갈릴 수 없다.

    Raises:
        ValueError: 학습되지 않은(또는 표본이 임계 미만인) 공종일 때, 그리고 부스터가
            아무 값도 내지 않았을 때.
    """
    unlearned = unlearned_category_reason(model, context.category)
    if unlearned is not None:
        raise ValueError(unlearned)
    rates = model.predict_rates(
        [(float(context.budget or 0.0), context.category, context.agency_name)]
    )
    if not rates:
        raise ValueError("Award-rate GBM produced no prediction for the context.")
    return clamp_bid_rate(rates[0])


def build_award_rate_gbm_prediction(
    context: PricePredictionContext, *, model: LoadedAwardRateGbmModel
) -> PredictionResult:
    """예측 → 시나리오 → 검증된 결과. 검증은 typed 생성자 단일 지점이다."""
    budget = float(context.budget or 0.0)
    center = _guarded_center_rate(context, model)
    candidates = build_scenario_candidates(
        center=center, std=model.residual_std, budget=budget
    )
    base_candidate = candidates[1]
    agency_samples = model.agency_sample_count(
        agency=context.agency_name, category=context.category
    )
    return PredictionResult(
        predicted_price=float(base_candidate["predicted_price"]),
        price_range_min=min(candidate["predicted_price"] for candidate in candidates),
        price_range_max=max(candidate["predicted_price"] for candidate in candidates),
        confidence_score=_estimate_confidence(model, agency_samples),
        model_version=model.model_version,
        pricing_mode=_PRICING_MODE,
        # 이 predictor 가 실제로 소비한 관측은 요청 컨텍스트의 이력 행이 아니라
        # **아티팩트의 학습 행**이다. context.historical_sample_size 를 실으면 한 응답이
        # 소비하지 않은 표본을 보고하고, 그 값이 PricePrediction 으로 영속화돼 정확도
        # 리포트까지 간다(#362 리뷰 L4-2 와 같은 사유).
        historical_sample_size=model.training_row_count,
        agency_match_sample_size=agency_samples,
        predicted_bid_rate=float(base_candidate["bid_rate"]),
        bid_rate_candidates=candidates,
        # 예비가격 패턴은 이 모델의 입력이 아니다 — 실으면 쓰지 않은 근거를 보고하게 된다.
        reserve_price_context=None,
        feedback_calibration=None,
        guardrail_applied=False,
        guardrail_reason=None,
        floor_bid_rate=None,
        floor_price=None,
        competitive_target_bid_rate=float(base_candidate["bid_rate"]),
        explanation=_build_explanation(model, agency_samples, center),
    )


def _estimate_confidence(model: LoadedAwardRateGbmModel, agency_samples: int) -> float:
    """기관 표본 깊이와 잔차 폭에서 신뢰도를 추정한다(선언 상수 §4.5)."""
    sample_score = min(agency_samples / _CONFIDENCE_SAMPLE_SATURATION, 1.0)
    tightness_score = max(
        0.0, 1.0 - min(model.residual_std / _CONFIDENCE_RESIDUAL_NORMALIZER, 1.0)
    )
    confidence = (
        _CONFIDENCE_BASE
        + (sample_score * _CONFIDENCE_SAMPLE_WEIGHT)
        + (tightness_score * _CONFIDENCE_TIGHTNESS_WEIGHT)
    )
    return round(max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, confidence)), 2)


def _build_explanation(
    model: LoadedAwardRateGbmModel, agency_samples: int, center: float
) -> str:
    """정직 명세(§2)에 맞는 설명 — 가격 적합도 추정이지 낙찰 확률 단정이 아니다.

    시나리오 폭을 "교차검증 잔차"라고 부르는 것은 정확성 때문이다: ``residual_std`` 는
    학습기의 **무작위 K-fold out-of-fold** 잔차(``_out_of_fold_matrix_and_residuals``)이지
    시간 홀드아웃 잔차가 아니다. 둘은 다른 수이고(전자는 시간 드리프트를 포함하지 않아
    더 좁다), 운영자에게 "홀드아웃"이라고 말하면 실제보다 검증된 폭으로 읽힌다.
    """
    agency_clause = (
        f"이 발주기관의 학습 표본은 {agency_samples}건입니다"
        if agency_samples
        else "이 발주기관은 학습 표본에 없어 공종 평균으로 수축했습니다"
    )
    return (
        f"공종·금액대·발주기관을 입력으로 학습한 낙찰률 모델(학습 "
        f"{model.training_row_count}건)이 기초금액 대비 낙찰률 {center:.4f}를 "
        f"추정했습니다. {agency_clause}. 보수/공격 시나리오는 교차검증 잔차 "
        f"표준편차 {model.residual_std:.4f}의 정규근사 ±1.28σ 경계입니다. "
        f"예정가·예비가격은 투찰 시점에 공개되지 않으므로 입력에 포함하지 않았습니다."
    )
