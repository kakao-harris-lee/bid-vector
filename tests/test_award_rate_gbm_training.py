"""낙찰률 GBM 학습기의 **누수 차단 계약** — out-of-fold 인코딩이 유일한 장치다.

발주기관 target encoding 은 라벨의 함수다. 자기 행의 라벨이 자기 피처에 들어가면 학습
오차만 낮아지고 홀드아웃에서 그 이득이 사라진다. 그 차단은 학습기의 폴드 루프 **한 줄**
(``build_feature_space(rows, encoding=_encoding_for(fit_rows))``)에 걸려 있는데, 그 한 줄을
``_encoding_for(rows)``(자기 라벨 포함)로 바꿔도 다른 테스트는 전부 통과한다 — 누수는
예외를 내지 않고 수치만 낙관적으로 만들기 때문이다.

그래서 여기서는 학습 행렬의 **인코딩 열 값 자체**를 본다: out-of-fold 로 만든 값은 전체
코퍼스로 만든 값과 달라야 하고(같으면 자기 라벨을 본 것이다), 자기 라벨이 특이할수록
그 차이는 자기 값에서 **멀어지는** 방향이어야 한다.
"""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.domain.award_rate_features import (
    AGENCY_ENCODING_FEATURE,
    AWARD_RATE_FEATURE_NAMES,
)
from app.services.ml_training.award_rate_gbm import (
    AwardRateTrainingRow,
    _feature_matrix,
    _out_of_fold_matrix_and_residuals,
    build_feature_space,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)
_ENCODING_INDEX = AWARD_RATE_FEATURE_NAMES.index(AGENCY_ENCODING_FEATURE)

# 기관마다 뚜렷하게 다른 수준 — 인코딩이 실제로 기관을 구별하게 만드는 픽스처.
_AGENCY_RATES = {"가기관": 0.86, "나기관": 0.92, "다기관": 0.98}

# 마지막 행에 심는 특이 라벨. 자기 라벨이 자기 인코딩에 들어가면 이 값이 그 행의
# 인코딩을 끌어내리므로, 누수 여부가 **부호**로 드러난다.
_OUTLIER_VALUE = 0.40


def _rows(count: int = 120) -> list[AwardRateTrainingRow]:
    agencies = list(_AGENCY_RATES)
    rows = [
        AwardRateTrainingRow(
            value=_AGENCY_RATES[agencies[index % len(agencies)]]
            + 0.002 * ((index % 5) - 2),
            amount=100_000_000.0,
            category="construction" if index % 2 else "service",
            agency=agencies[index % len(agencies)],
            denominator_source="clean-base",
            opened_at=_START + timedelta(hours=index),
            published_floor_rate=0.88 if index % 3 else None,
        )
        for index in range(count)
    ]
    last = rows[-1]
    rows[-1] = AwardRateTrainingRow(
        value=_OUTLIER_VALUE,
        amount=last.amount,
        category=last.category,
        agency=last.agency,
        denominator_source=last.denominator_source,
        opened_at=last.opened_at,
        published_floor_rate=last.published_floor_rate,
    )
    return rows


def _encoding_columns(
    rows: list[AwardRateTrainingRow], *, folds: int = 3, seed: int = 11
) -> tuple[np.ndarray, np.ndarray]:
    """(학습 행렬의 인코딩 열, 전체 코퍼스 인코딩으로 만든 같은 열)."""
    out_of_fold, _residuals = _out_of_fold_matrix_and_residuals(
        rows, folds=folds, seed=seed
    )
    full = _feature_matrix(rows, build_feature_space(rows))
    return out_of_fold[:, _ENCODING_INDEX], full[:, _ENCODING_INDEX]


def test_training_matrix_encoding_differs_from_the_self_including_encoding():
    """학습 행렬의 인코딩이 전체 인코딩과 같다면 각 행이 자기 라벨을 본 것이다."""
    rows = _rows()
    out_of_fold, full = _encoding_columns(rows)

    assert not np.allclose(out_of_fold, full)
    # 일부가 아니라 **모든** 행이 달라야 한다 — 한 폴드라도 전체 인코딩을 쓰면 그
    # 폴드의 행들이 여기서 같아진다.
    assert np.count_nonzero(np.abs(out_of_fold - full) > 1e-12) == len(rows)


def test_an_outlier_label_does_not_pull_down_its_own_encoding():
    """자기 라벨이 자기 피처에 들어가면 특이값이 자기 인코딩을 끌어내린다(부호 검사)."""
    rows = _rows()
    out_of_fold, full = _encoding_columns(rows)

    assert rows[-1].value == _OUTLIER_VALUE
    # 전체 인코딩은 특이값에 눌려 있고, out-of-fold 값은 그 영향에서 자유롭다.
    assert out_of_fold[-1] > full[-1]
