"""낙찰률 GBM 학습기 — 행 목록 → 서빙 아티팩트 (Phase 2).

무엇을 학습하는가
-----------------
타깃은 **basis 명시 낙찰률**(:mod:`app.domain.award_rate_label` 의 ``value``)이고, 근거
있는 분모만 받는다(``status == "ok"``). 사정률이 아니라 낙찰률을 학습하는 이유는 사정률
분산의 대부분이 투찰 시점에 관측 불가(열린 공고는 예비가격 미공개)이고 나머지가 추첨
난수라, 학습 대상이 될 신호가 없기 때문이다(Phase 1 이 그 하한을 못 박았다).

피처는 :class:`~app.domain.award_rate_features.AwardRateFeatureSpace` 가 조립한다 —
학습과 서빙이 **같은 함수**를 부르는 것이 train/serve skew 를 막는 구조적 장치다. 이
모듈은 그 위에 두 가지를 얹는다: (a) 발주기관 인코딩의 out-of-fold 조립, (b) LightGBM
부스팅.

왜 out-of-fold 인코딩인가
-------------------------
발주기관 인코딩은 라벨의 함수다. 자기 행의 라벨이 자기 피처에 들어가면 학습 오차만
낮아지고 홀드아웃에서는 그 이득이 사라진다(target leakage). 그래서 학습 행렬은 폴드별로
"그 폴드를 뺀 나머지"로 만든 인코딩을 쓰고, 서빙 아티팩트에는 전체 학습 구간으로 만든
인코딩을 싣는다.

폴드 분할은 시간이 아니라 무작위다. 시간 누수는 **학습 구간 자체를 cutoff 로 자르는
호출부**(``load_award_rate_training_rows(db, cutoff_at=...)``)가 막고, 폴드는 학습 구간
안에서 인코딩의 자기참조만 끊는다. 두 장치는 목적이 다르므로 겹치지 않는다.

잔차 표준편차도 out-of-fold 로 잰다. 자기 학습 행에 대고 재면 부스팅이 이미 맞춘 값이라
과소평가되고, 그 값이 그대로 시나리오 후보 폭(보수/공격)이 되어 운영자에게 실제보다 좁은
구간을 보여 준다.

의존성 경계
-----------
``lightgbm`` 은 **런타임 requirements 가 아니다**(``requirements/ml-training.txt`` 전용).
API 프로세스가 ML 을 로드하지 않게 하려는 경계이므로, import 는 함수 안에서 지연시킨다.
학습은 training-worker 큐에서만 돈다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from pydantic import JsonValue

from app.ai.predictors.artifact_contracts import (
    PersistedAgencyEncodingEntry,
    PersistedAwardRateGbmArtifact,
)
from app.domain.award_rate_features import (
    AWARD_RATE_FEATURE_NAMES,
    CATEGORICAL_AWARD_RATE_FEATURES,
    AgencyTargetEncoding,
    AwardRateFeatureSpace,
    AwardRateObservation,
    build_agency_target_encoding,
    normalize_feature_key,
)

if TYPE_CHECKING:  # pragma: no cover - lightgbm 은 런타임 의존성이 아니다(경계 유지)
    import lightgbm as lgb

__all__ = [
    "AWARD_RATE_GBM_ARTIFACT_VERSION",
    "AWARD_RATE_GBM_MODEL_VERSION",
    "AwardRateTrainingRow",
    "build_feature_space",
    "train_award_rate_gbm",
]

# 아티팩트 계약 버전. 피처 목록·인코딩 모양이 바뀌면 올린다 — predictor 는 이 값이
# 다르면 모델을 쓰지 않는다(다른 피처 공간으로 학습된 부스터를 태우면 조용히 틀린다).
# v2: 공시 낙찰하한율 두 축(``published_floor_rate`` · ``has_published_floor``) 추가.
# v1 아티팩트는 피처 목록이 달라 로드 시 거부된다(``different feature set``).
AWARD_RATE_GBM_ARTIFACT_VERSION: Final[str] = "award-rate-gbm-v2"
AWARD_RATE_GBM_MODEL_VERSION: Final[str] = "v2.0-award-rate-gbm"

# LightGBM 하이퍼파라미터 — 선언 데이터(§4.5-1). 표본 수만 행 · 피처 일곱 개 규모의 얕은
# 표 형태 회귀라, 트리를 깊게 키우기보다 낮은 학습률로 많은 라운드를 도는 쪽이 안정적이다.
#
# ``deterministic`` + ``force_row_wise`` + 고정 seed 로 같은 입력이 같은 아티팩트를 낸다.
# ``num_threads`` 를 **고정하는 것도 그 재현성의 일부**다: LightGBM 의 결정성 보장은
# 같은 스레드 수를 전제하므로, 코어 수가 다른 호스트에서 학습하면 아티팩트가 갈린다.
# 값이 작은 이유는 별개다 — 이 배포는 API·워커·DB 가 한 호스트를 공유하므로 학습이
# 전 코어를 잡으면 수집·추론 큐가 굶는다.
LIGHTGBM_PARAMS: Final[dict[str, Any]] = {
    "objective": "regression",
    "metric": "rmse",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
}
BOOSTING_ROUNDS: Final[int] = 400
DEFAULT_ENCODING_FOLDS: Final[int] = 5
DEFAULT_TRAINING_SEED: Final[int] = 20260812

# 잔차 표준편차의 하한. 표본이 얕아 잔차가 0 에 가깝게 나와도 "다음 공고의 낙찰률이
# 정확히 이 값"이라는 주장은 정직하지 않으므로 최소 폭을 남긴다(사정률 커널의
# MIN_PREDICTIVE_STD 와 같은 스탠스, 값은 낙찰률 축의 해상도에 맞춘다).
MIN_RESIDUAL_STD: Final[float] = 0.002


@dataclass(frozen=True)
class AwardRateTrainingRow:
    """학습 행 한 건 — 라벨과 피처 원재료만. ORM 도 예비가격도 들어오지 않는다.

    ``amount`` 는 라벨의 **분모**(신뢰 기초금액)다. 라벨이 ``낙찰가 ÷ amount`` 이므로
    피처의 금액과 라벨의 분모가 같은 수여야 하고, 서빙에서 그 자리를 채우는 것은
    ``PricePredictionContext.budget``(기초금액)이다 — 축이 같다.

    ``opened_at`` 은 피처가 아니다. 호출부의 cutoff 필터와 홀드아웃 분할에만 쓰이며,
    피처 조립기(``AwardRateFeatureSpace.build_row``)는 시각을 받지 않는다.

    ``published_floor_rate`` 는 **공고가 게시한** 낙찰하한율(개연 밴드 통과분)이다.
    공고문에 있는 값이라 개찰 후 정보가 아니고, 유일한 백필러가 열린 공고만 대상으로
    하므로(``scripts/backfill_award_floor_rate.py``) 정산 행에 사후 주입되지 않는다.
    """

    value: float
    amount: float
    category: str
    agency: str
    denominator_source: str
    opened_at: datetime
    # 기본값을 주지 않는다: "안 넘겼다"와 "미공시"는 다른 상태이고, 새 행 생산자가
    # 조용히 전자를 후자로 보고하면 그 축이 통째로 결측으로 학습된다.
    published_floor_rate: float | None


def build_feature_space(
    rows: Sequence[AwardRateTrainingRow],
    *,
    encoding: AgencyTargetEncoding | None = None,
) -> AwardRateFeatureSpace:
    """행 목록에서 어휘를 뽑고 인코딩 표와 묶어 피처 공간을 만든다.

    어휘는 **정렬된 관측 집합**이다. 정렬하지 않으면 같은 코퍼스에서 두 번 학습할 때
    범주 코드가 달라져 아티팩트가 재현되지 않는다.
    """
    categories = tuple(sorted({normalize_feature_key(row.category) for row in rows}))
    sources = tuple(sorted({row.denominator_source for row in rows}))
    return AwardRateFeatureSpace(
        categories=categories,
        denominator_sources=sources,
        agency_encoding=encoding or _encoding_for(rows),
    )


def _encoding_for(rows: Sequence[AwardRateTrainingRow]) -> AgencyTargetEncoding:
    """행 목록으로 계층 수축 인코딩 표를 만든다(도메인 커널 위임)."""
    return build_agency_target_encoding(
        AwardRateObservation(
            agency=row.agency, category=row.category, value=row.value
        )
        for row in rows
    )


def _feature_matrix(
    rows: Sequence[AwardRateTrainingRow], space: AwardRateFeatureSpace
) -> np.ndarray:
    """행 목록을 피처 행렬로 — 조립은 도메인 커널 한 지점만 부른다."""
    return np.array(
        [
            space.build_row(
                amount=row.amount,
                category=row.category,
                agency=row.agency,
                denominator_source=row.denominator_source,
                published_floor_rate=row.published_floor_rate,
            )
            for row in rows
        ],
        dtype=float,
    ).reshape(len(rows), len(AWARD_RATE_FEATURE_NAMES))


def _fold_indices(row_count: int, *, folds: int, seed: int) -> list[np.ndarray]:
    """무작위 K-fold 인덱스(재현 가능). 시간 분할이 아니다 — 모듈 docstring 참조."""
    permutation = np.random.default_rng(seed).permutation(row_count)
    return [part for part in np.array_split(permutation, folds) if part.size]


def _categorical_indices() -> list[int]:
    return [
        index
        for index, name in enumerate(AWARD_RATE_FEATURE_NAMES)
        if name in CATEGORICAL_AWARD_RATE_FEATURES
    ]


def _train_booster(
    matrix: np.ndarray, targets: np.ndarray, *, seed: int
) -> "lgb.Booster":
    """LightGBM 부스터 한 개. ``lightgbm`` import 는 여기서 지연된다(경계 유지)."""
    import lightgbm as lgb

    dataset = lgb.Dataset(
        matrix,
        label=targets,
        feature_name=list(AWARD_RATE_FEATURE_NAMES),
        categorical_feature=_categorical_indices(),
        free_raw_data=False,
    )
    return lgb.train(
        {**LIGHTGBM_PARAMS, "seed": seed},
        dataset,
        num_boost_round=BOOSTING_ROUNDS,
    )


def _out_of_fold_matrix_and_residuals(
    rows: Sequence[AwardRateTrainingRow],
    *,
    folds: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """폴드별 인코딩으로 만든 학습 행렬과, 폴드별 모델의 out-of-fold 잔차.

    한 번의 폴드 순회로 둘을 같이 얻는다: 각 폴드에서 (a) 나머지 행으로 인코딩을 만들어
    그 폴드의 피처를 조립하고, (b) 나머지 행으로 부스터를 학습해 그 폴드를 예측한다.
    두 산출 모두 "자기 라벨을 보지 않은" 값이라, 학습 행렬에는 누수가 없고 잔차는
    낙관 편향이 없다.
    """
    row_count = len(rows)
    matrix = np.zeros((row_count, len(AWARD_RATE_FEATURE_NAMES)), dtype=float)
    residuals = np.zeros(row_count, dtype=float)
    targets = np.array([row.value for row in rows], dtype=float)

    for held_out in _fold_indices(row_count, folds=folds, seed=seed):
        held_out_set = set(held_out.tolist())
        fit_rows = [row for index, row in enumerate(rows) if index not in held_out_set]
        if not fit_rows:
            continue
        fold_rows = [rows[index] for index in held_out]
        fold_space = build_feature_space(rows, encoding=_encoding_for(fit_rows))
        fold_matrix = _feature_matrix(fold_rows, fold_space)
        matrix[held_out] = fold_matrix
        booster = _train_booster(
            _feature_matrix(fit_rows, fold_space),
            np.array([row.value for row in fit_rows], dtype=float),
            seed=seed,
        )
        residuals[held_out] = booster.predict(fold_matrix) - targets[held_out]

    return matrix, residuals


def _serialize_encoding(
    encoding: AgencyTargetEncoding,
) -> list[PersistedAgencyEncodingEntry]:
    """인코딩 표를 배열로 편다 — dict 키가 튜플이라 그대로는 직렬화되지 않는다.

    정렬해 싣는다: 같은 코퍼스가 같은 바이트를 내야 아티팩트 diff 가 의미를 갖는다.
    """
    return [
        PersistedAgencyEncodingEntry(
            agency=agency, category=category, mean=mean, count=count
        )
        for (agency, category), (mean, count) in sorted(encoding.agency_means.items())
    ]


def _residual_std(residuals: np.ndarray) -> float:
    """out-of-fold 잔차의 표준편차 — 시나리오 후보 폭이 되므로 하한을 둔다."""
    if residuals.size <= 1:
        return MIN_RESIDUAL_STD
    return max(float(np.std(residuals, ddof=1)), MIN_RESIDUAL_STD)


def _assemble_artifact(
    rows: Sequence[AwardRateTrainingRow],
    *,
    space: AwardRateFeatureSpace,
    booster: "lgb.Booster",
    residuals: np.ndarray,
) -> PersistedAwardRateGbmArtifact:
    """학습 산출물을 아티팩트 계약으로 조립한다 — 계약 위반은 여기서 예외가 된다."""
    encoding = space.agency_encoding
    return PersistedAwardRateGbmArtifact(
        artifact_version=AWARD_RATE_GBM_ARTIFACT_VERSION,
        model_version=AWARD_RATE_GBM_MODEL_VERSION,
        feature_names=list(AWARD_RATE_FEATURE_NAMES),
        categories=list(space.categories),
        denominator_sources=list(space.denominator_sources),
        agency_encoding=_serialize_encoding(encoding),
        category_means=dict(sorted(encoding.category_means.items())),
        global_mean=encoding.global_mean,
        residual_std=_residual_std(residuals),
        booster_model=str(booster.model_to_string()),
        training_row_count=len(rows),
        training_through=max(row.opened_at for row in rows).isoformat(),
    )


def train_award_rate_gbm(
    rows: Sequence[AwardRateTrainingRow],
    *,
    folds: int = DEFAULT_ENCODING_FOLDS,
    seed: int = DEFAULT_TRAINING_SEED,
) -> dict[str, JsonValue]:
    """학습 행 목록에서 서빙 아티팩트의 **JSON 페이로드**를 만든다.

    조립은 :class:`~app.ai.predictors.artifact_contracts.PersistedAwardRateGbmArtifact`
    로 하되(그 순간 계약이 검증되므로 학습기가 계약을 어긴 산출물을 만들 수 없다),
    **내보내는 것은 직렬화된 dict** 다. 검증된 모델 객체를 그대로 넘기지 않는 이유는
    그 계약 모델의 존재 이유가 **로드 시점의 fail-closed 검증**이기 때문이다: 학습기가
    이미 복원된 객체를 건네면 로더의 계약 검증이 in-memory 경로에서 死코드가 되고,
    홀드아웃 커널(:mod:`app.services.ml_training.award_rate_holdout`)이 주장하는
    "직렬화·복원까지 포함한 **서빙과 같은 경로**"가 거짓이 된다. dict 로 내보내면
    파일 경로든 임베드 매핑이든 로더의 검증을 똑같이 통과해야 한다.

    Args:
        rows: 학습 행. **cutoff 필터는 호출부 책임**이다 — 이 함수는 시각을 보지 않고,
            ``opened_at`` 은 아티팩트의 학습 구간 표기에만 쓴다.
        folds: 인코딩·잔차의 out-of-fold 분할 수.
        seed: 폴드 순열과 부스팅의 난수 seed(재현성).

    Returns:
        JSON 직렬화 가능한 아티팩트 페이로드. 그대로 파일로 쓰거나
        :func:`~app.ai.predictors.award_rate_gbm.load_award_rate_gbm_model` 에 넘긴다.

    Raises:
        ValueError: 행이 비었을 때. 최소 표본 게이트(운영값)는 호출부가 건다.
    """
    if not rows:
        raise ValueError("Award-rate GBM training needs at least one labelled row.")

    matrix, residuals = _out_of_fold_matrix_and_residuals(rows, folds=folds, seed=seed)
    booster = _train_booster(
        matrix, np.array([row.value for row in rows], dtype=float), seed=seed
    )
    artifact = _assemble_artifact(
        rows,
        space=build_feature_space(rows),
        booster=booster,
        residuals=residuals,
    )
    return artifact.model_dump(mode="json")
