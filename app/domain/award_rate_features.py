"""낙찰률 GBM 의 피처 공간 — 학습과 서빙이 **같은 한 벌**로 행을 만든다 (Phase 2).

왜 피처 조립을 도메인 커널로 올리는가
--------------------------------------
학습 파이프라인과 서빙 predictor 가 각자 피처를 조립하면, 둘이 어긋나는 순간 모델은
학습 때 본 적 없는 좌표에서 외삽한다(train/serve skew). 그 어긋남은 예외를 내지 않고
**조용히 가격만 바꾸므로** 테스트가 잡기 어렵다. 그래서 행 조립을
:meth:`AwardRateFeatureSpace.build_row` **한 지점**으로 좁히고, 학습기와 predictor 가
그 함수만 부른다. 피처 목록이 바뀌면 두 경로가 동시에 바뀌거나 둘 다 실패한다.

같은 이유로 이 커널의 입력 시그니처가 **곧 피처 계약**이다: 금액·공종·발주기관·분모
출처 넷뿐이고, 그 밖의 것(예비가격·추첨번호·실현 사정률·낙찰가)은 아예 **받지 않는다**.
대상 공고의 예비가격 파생값은 투찰 시점에 존재하지 않으므로(열린 공고는 예비가격이
공개되기 전이다) 피처가 될 수 없는데, "쓰지 말자"는 규율보다 "받을 수 없다"는 시그니처가
강하다. 이 배제를 ``tests/test_award_rate_features.py`` 의 계약 테스트가 고정한다.

발주기관 축을 원-핫이 아니라 수축 인코딩으로 두는 이유
------------------------------------------------------
발주기관은 고카디널리티다(학습 코퍼스 수천 곳, 대부분 표본 한 자릿수 — 실측은 PR 본문).
원-핫은 차원만 늘리고 얕은 기관에서 노이즈를 그대로 싣는다. 대신 기관별 라벨 평균을
**기관 → 공종 → 전역** pseudo-count 수축으로 접어 하나의 수치 피처로 만든다. 수축
산술은 :func:`~app.domain.assessment_shrinkage.shrink_toward` **한 벌**을 그대로 쓴다
(§4.5-8 — 같은 알고리즘을 두 벌로 두면 두 축의 수축 강도가 조용히 갈린다). 그 모듈의
κ 는 사정률 축에서 도출된 값이라 여기서는 재사용하지 않고, 낙찰률 축의 κ 를 아래에
따로 선언한다.

인코딩은 **누수 경로**이기도 하다: 자기 자신의 라벨이 자기 피처에 들어가면 학습 오차만
낮아진다. 그래서 학습 행렬은 out-of-fold 로 조립하고(폴드 분할은 학습기 책임 —
:mod:`app.services.ml_training.award_rate_gbm`), 이 커널은 "주어진 관측 집합으로 인코딩
표를 만든다"까지만 한다.

분모 출처(``denominator_source``)를 왜 피처로 싣는가
-----------------------------------------------------
학습 타깃 :class:`~app.domain.award_rate_label.AwardRateLabel` 의 ``ok`` 는 동질 집단이
아니다 — ``clean-base``(관측된 기초금액)와 ``reserve-estimate``(복수예비가격 midpoint 로
복구한 추정 기초금액)가 섞이고, 두 층의 라벨 평균이 공종별로 갈린다(그 라벨 모듈의
"``ok`` 안의 두 티어" 참조). 출처를 피처로 실으면 학습기가 층 효과를 분리할 수 있고,
서빙은 :data:`SERVING_DENOMINATOR_SOURCE` 로 **고정**한다 — 라이브 공고의 기초금액은
관측값이지 복구 추정치가 아니므로 ``clean-base`` 층으로 예측하는 것이 축 정합이다.

출처를 아예 빼고 두 층을 뭉쳐 학습하면 예측 수준이 두 층의 혼합 평균으로 내려앉는다.
그 편이 특정 홀드아웃에서 더 좋아 보일 수 있지만(층 간 평균 차이가 그 구간의 시간 드리프트와
우연히 같은 방향이면), 그것은 신호가 아니라 상쇄다 — 수치와 판단 근거는 PR 본문.

순수 함수(값 입력 → 값 출력, I/O 0). stdlib + 기존 strict 아일랜드만 의존. mypy strict.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import log10, log1p
from types import MappingProxyType
from typing import Final, TypeVar

from app.domain.assessment_shrinkage import shrink_toward
from app.domain.reliable_base import ReliableBaseSource
from app.utils.textfmt import normalize_lookup_key

# 피처 이름 — 학습 아티팩트에 그대로 직렬화되고, predictor 는 아티팩트의 이름 목록이
# 이 선언과 일치할 때만 모델을 쓴다(불일치 = 다른 피처 공간으로 학습된 모델).
CATEGORY_FEATURE: Final[str] = "category"
LOG_AMOUNT_FEATURE: Final[str] = "log_amount"
AGENCY_ENCODING_FEATURE: Final[str] = "agency_encoding"
AGENCY_SAMPLE_FEATURE: Final[str] = "agency_sample_count"
DENOMINATOR_SOURCE_FEATURE: Final[str] = "denominator_source"

AWARD_RATE_FEATURE_NAMES: Final[tuple[str, ...]] = (
    CATEGORY_FEATURE,
    LOG_AMOUNT_FEATURE,
    AGENCY_ENCODING_FEATURE,
    AGENCY_SAMPLE_FEATURE,
    DENOMINATOR_SOURCE_FEATURE,
)

# 어휘 인덱스로 실리는(=LightGBM 이 범주로 다루는) 피처. 나머지는 연속값이다.
CATEGORICAL_AWARD_RATE_FEATURES: Final[frozenset[str]] = frozenset(
    {CATEGORY_FEATURE, DENOMINATOR_SOURCE_FEATURE}
)

# 어휘에 없는 값의 코드. LightGBM 은 음수 범주 코드를 결측으로 다루므로, 처음 보는
# 공종/출처는 "그 축을 모른다"로 떨어지고 다른 축이 예측을 낸다. 0(=첫 어휘)으로 접으면
# 미지의 값이 특정 공종인 척하게 된다.
UNKNOWN_CATEGORY_CODE: Final[float] = -1.0

# 서빙 시 고정하는 분모 출처. 라이브 공고의 기초금액은 수집된 관측값이라 복구 추정
# 층이 아니다 — 모듈 docstring "분모 출처를 왜 피처로 싣는가" 참조.
SERVING_DENOMINATOR_SOURCE: Final[ReliableBaseSource] = ReliableBaseSource.CLEAN_BASE

# 낙찰률 축의 pseudo-count 수축 강도(κ, 등가 표본 수). ``assessment_shrinkage`` 의
# κ(기관 12 / 공종 40)는 **사정률 축**에서 도출된 값이라 그대로 가져오지 않고, 같은
# 산술(``shrink_toward``)만 재사용한 채 이 축의 값을 따로 선언한다.
#
# 기관 κ: 학습 코퍼스에서 기관 대부분이 표본 한 자릿수라 자기 평균 점추정이 성립하지
# 않는다. κ 를 키우면 기관 축이 공종 평균으로 접히고, 줄이면 얕은 기관의 노이즈가
# 그대로 피처에 실린다. 값은 out-of-time 홀드아웃 민감도로 골랐고 넓은 평탄 구간
# 안에 있다(측정값은 PR 본문 — 파일에 적어두면 데이터가 늘 때 조용히 낡는다).
# 공종 κ: 공종은 코퍼스에 서너 그룹뿐이고 각 그룹 표본이 수천이라 n/(n+κ)≈1 이다.
# 즉 이 κ 는 표본이 수십 건뿐인 신생 공종만 전역으로 접는 안전장치다.
AWARD_RATE_AGENCY_PRIOR_STRENGTH: Final[float] = 12.0
AWARD_RATE_CATEGORY_PRIOR_STRENGTH: Final[float] = 40.0

# log10 금액의 하한 가드. 0원/음수 금액은 실적이 아니라 적재 사고이고, log 는 거기서
# 정의되지 않는다. 1.0(=log10 0)으로 바닥을 깔아 예외 대신 최소 좌표로 떨어뜨린다.
_MIN_FEATURE_AMOUNT: Final[float] = 1.0

# 별칭 해소를 하지 않는다는 **선언**. 학습 코퍼스(``HistoricalData.category``)와 서빙
# 컨텍스트(``Project.category``)가 같은 수집 파이프라인에서 나온 같은 어휘를 쓰므로
# (실측 확인) 별칭 표가 필요 없고, 표를 끼우면 학습 어휘에 없는 철자가 조용히 다른
# 공종으로 접혀 축이 갈린다. 처음 보는 값은 접지 않고 미지 코드로 떨어뜨린다.
_NO_ALIASES: Final[Mapping[str, str]] = {}


def normalize_feature_key(value: str | None) -> str:
    """공종·발주기관 키를 소문자 정규화한다 (양쪽 경로 단일 출처).

    학습 집계와 서빙 조회가 같은 키를 만들어야 인코딩 표가 실제로 조회된다 — 한쪽만
    대소문자를 접으면 서빙에서 모든 기관이 미관측으로 떨어지고, 그 실패는 예외 없이
    "전역 평균으로 예측"으로만 나타난다.
    """
    return normalize_lookup_key(value, _NO_ALIASES)


@dataclass(frozen=True)
class AwardRateObservation:
    """인코딩 표를 만들 때 쓰는 관측 한 건(값만 — ORM 도, 시각도 없다)."""

    agency: str
    category: str
    value: float


@dataclass(frozen=True)
class AgencyTargetEncoding:
    """발주기관 → 공종 → 전역으로 수축된 라벨 평균 표.

    ``agency_means`` 의 키는 (기관 키, 공종 키)다. 기관을 공종과 함께 잡는 이유는 한
    기관이 공사와 용역을 동시에 발주할 때 두 축의 낙찰률 수준이 다르기 때문이다 —
    기관만으로 잡으면 그 기관의 공종 구성비가 인코딩에 섞여 든다.

    두 표는 생성 시점에 **사본을 읽기 전용으로 봉인**한다. ``frozen=True`` 는 필드 재바인딩만
    막을 뿐 표 내용의 변형은 막지 못하는데, 서빙 경로는 복원된 이 값을 프로세스 캐시에서
    **공유**하므로(:class:`~app.ai.predictors.award_rate_gbm.LoadedAwardRateGbmModel`)
    한 호출부의 표 변형이 이후 모든 예측을 바꿀 수 있다. 봉인은 그 표면을 없앤다.
    """

    agency_means: Mapping[tuple[str, str], tuple[float, int]]
    category_means: Mapping[str, float]
    global_mean: float

    def __post_init__(self) -> None:
        """표를 사본 위의 읽기 전용 뷰로 교체한다(호출부의 원본 dict 와도 분리)."""
        object.__setattr__(
            self, "agency_means", MappingProxyType(dict(self.agency_means))
        )
        object.__setattr__(
            self, "category_means", MappingProxyType(dict(self.category_means))
        )

    def category_sample_count(self, category: str) -> int:
        """이 공종을 뒷받침한 학습 표본 수. 어휘에 없으면 ``0``.

        아티팩트에 이미 실려 있는 것에서 **유도**한다 — ``agency_means`` 의 키가
        (기관, 공종)이고 값이 (수축 평균, 관측 수)라, 한 공종에 대해 기관을 가로질러
        더하면 그 공종의 관측 수가 정확히 나온다(집계 시 모든 관측이 기관 키를 갖는다 —
        기관이 비어 있어도 빈 문자열 키로 계수된다). 새 아티팩트 필드를 만들지 않은
        이유가 이것이다: 계약을 늘리지 않으면 기존 산출물도 그대로 판정할 수 있다.
        """
        return sum(
            count
            for (_agency, entry_category), (_mean, count) in self.agency_means.items()
            if entry_category == category
        )

    def encode(self, *, agency: str, category: str) -> tuple[float, int]:
        """(수축된 평균, 그 평균을 뒷받침한 기관 표본 수).

        미관측 기관은 공종 평균(그것도 없으면 전역 평균)을 표본 수 0 으로 낸다. 값과
        표본 수를 함께 내는 것이 계약이다: 학습기는 표본 수를 별도 피처로 실어 "이
        인코딩을 얼마나 믿을지"를 모델이 직접 배우게 한다.
        """
        observed = self.agency_means.get((agency, category))
        if observed is not None:
            return observed
        return self.category_means.get(category, self.global_mean), 0


@dataclass(frozen=True)
class AwardRateFeatureSpace:
    """피처 공간 전체 — 어휘 + 인코딩 표. 아티팩트에 그대로 실려 서빙으로 건너간다.

    어휘를 아티팩트에 싣는 이유는 범주 코드가 **위치**이기 때문이다. 서빙이 어휘를
    다시 만들면 코퍼스가 늘 때 순서가 바뀌어 같은 공종이 다른 코드로 들어가고, 모델은
    예외 없이 다른 잎을 탄다.
    """

    categories: tuple[str, ...]
    denominator_sources: tuple[str, ...]
    agency_encoding: AgencyTargetEncoding

    def category_training_rows(self, category: str | None) -> int:
        """이 공종을 배운 학습 행 수 — 서빙 가용성 판정의 **단일 근거**.

        "본 적 없다"(0)와 "너무 얕다"(작은 양수)를 **한 수**로 답한다. 두 상태는 운영자에게
        는 다른 사실이지만(호출부가 문구를 나눈다) 판정 규칙은 하나여야 한다 — 규칙을 둘로
        나누면 임계가 두 곳에서 갈릴 수 있다.

        어휘에 없는 공종이 위험한 이유는 예외가 나지 않기 때문이다: ``build_row`` 가
        :data:`UNKNOWN_CATEGORY_CODE` 를 내고 LightGBM 은 그것을 결측으로 다뤄, 모델은
        **다른 공종의 행으로 학습한 잎**을 태워 그럴듯한 투찰율을 낸다. 배운 적 없는
        공종에 대해 자신 있게 답하는 셈이라 정직 명세(§2) 위반이고 가격 사고다.
        """
        return self.agency_encoding.category_sample_count(
            normalize_feature_key(category)
        )

    def build_row(
        self,
        *,
        amount: float,
        category: str | None,
        agency: str | None,
        denominator_source: str,
    ) -> tuple[float, ...]:
        """한 공고의 피처 행. 학습·서빙이 공유하는 **유일한** 조립 지점이다.

        Args:
            amount: 라벨 분모와 같은 축의 금액(기초금액). 학습에서는 라벨의
                ``denominator_value``, 서빙에서는 ``PricePredictionContext.budget`` 이다
                — 둘이 같은 축이라는 것이 이 피처가 성립하는 조건이다.
            category: 공종 원문. 정규화·어휘 조회는 이 함수가 한다.
            agency: 발주기관 원문(없으면 미관측으로 떨어진다).
            denominator_source: 분모 출처. 서빙은
                :data:`SERVING_DENOMINATOR_SOURCE` 를 넘긴다.

        Returns:
            :data:`AWARD_RATE_FEATURE_NAMES` 와 같은 순서의 값 튜플.
        """
        category_key = normalize_feature_key(category)
        agency_key = normalize_feature_key(agency)
        encoded, sample_count = self.agency_encoding.encode(
            agency=agency_key, category=category_key
        )
        return (
            _vocabulary_code(self.categories, category_key),
            log10(max(float(amount), _MIN_FEATURE_AMOUNT)),
            encoded,
            log1p(float(sample_count)),
            _vocabulary_code(self.denominator_sources, denominator_source),
        )


def _vocabulary_code(vocabulary: Sequence[str], key: str) -> float:
    """어휘 위치를 코드로 — 없으면 미지 코드(결측)."""
    try:
        return float(vocabulary.index(key))
    except ValueError:
        return UNKNOWN_CATEGORY_CODE


@dataclass(frozen=True)
class _ObservationTotals:
    """수축 이전의 원시 집계 — 계층별 (합, 개수)와 전역 평균.

    수축은 이 집계의 **함수**다. 집계와 수축을 나눠 두면 κ 를 바꿔도 관측을 다시 훑지
    않고, 두 계층의 수축이 같은 입력에서 나온다는 것이 시그니처로 드러난다.
    """

    agency: dict[tuple[str, str], tuple[float, int]]
    category: dict[str, tuple[float, int]]
    global_mean: float


def _collect_totals(
    observations: Iterable[AwardRateObservation],
) -> _ObservationTotals:
    """관측을 한 번 훑어 기관·공종·전역 집계를 동시에 쌓는다(키 정규화 포함)."""
    agency_totals: dict[tuple[str, str], tuple[float, int]] = {}
    category_totals: dict[str, tuple[float, int]] = {}
    global_sum = 0.0
    global_count = 0
    for observation in observations:
        agency_key = normalize_feature_key(observation.agency)
        category_key = normalize_feature_key(observation.category)
        value = float(observation.value)
        global_sum += value
        global_count += 1
        _accumulate(category_totals, category_key, value)
        _accumulate(agency_totals, (agency_key, category_key), value)
    return _ObservationTotals(
        agency=agency_totals,
        category=category_totals,
        global_mean=global_sum / global_count if global_count else 0.0,
    )


def _shrunk_category_means(
    totals: _ObservationTotals, *, prior_strength: float
) -> dict[str, float]:
    """공종 평균을 전역 평균으로 수축한다 — 표본이 얕은 신생 공종만 실제로 접힌다."""
    return {
        key: shrink_toward(
            total / count,
            count,
            prior_mean=totals.global_mean,
            prior_strength=prior_strength,
        )[0]
        for key, (total, count) in totals.category.items()
    }


def _shrunk_agency_means(
    totals: _ObservationTotals,
    category_means: Mapping[str, float],
    *,
    prior_strength: float,
) -> dict[tuple[str, str], tuple[float, int]]:
    """기관 평균을 **자기 공종** 평균(없으면 전역)으로 수축하고 표본 수를 함께 낸다."""
    return {
        key: (
            shrink_toward(
                total / count,
                count,
                prior_mean=category_means.get(key[1], totals.global_mean),
                prior_strength=prior_strength,
            )[0],
            count,
        )
        for key, (total, count) in totals.agency.items()
    }


def build_agency_target_encoding(
    observations: Iterable[AwardRateObservation],
    *,
    agency_prior_strength: float = AWARD_RATE_AGENCY_PRIOR_STRENGTH,
    category_prior_strength: float = AWARD_RATE_CATEGORY_PRIOR_STRENGTH,
) -> AgencyTargetEncoding:
    """관측 집합에서 계층 수축 인코딩 표를 만든다 (순수).

    전역 → 공종 → 기관 순으로 상위 평균을 사전분포로 삼아 두 단계 수축한다. 수축
    산술은 :func:`~app.domain.assessment_shrinkage.shrink_toward` 한 벌이다.

    Args:
        observations: 인코딩을 만들 관측. **호출부가 시간 누수를 책임진다** — 학습
            행렬은 out-of-fold, 홀드아웃 평가는 cutoff 이전 관측만 넘겨야 한다. 이
            커널은 시각을 보지 않으므로 여기서 그 규칙을 강제할 수 없다.
        agency_prior_strength: 기관 수축 κ.
        category_prior_strength: 공종 수축 κ.

    Returns:
        :class:`AgencyTargetEncoding`. 관측이 비면 세 표가 모두 비고 전역 평균은
        ``0.0`` 이다 — 호출부(학습기)가 최소 표본 게이트로 먼저 막는다.
    """
    totals = _collect_totals(observations)
    category_means = _shrunk_category_means(
        totals, prior_strength=category_prior_strength
    )
    return AgencyTargetEncoding(
        agency_means=_shrunk_agency_means(
            totals, category_means, prior_strength=agency_prior_strength
        ),
        category_means=category_means,
        global_mean=totals.global_mean,
    )


_TotalsKeyT = TypeVar("_TotalsKeyT")


def _accumulate(
    totals: dict[_TotalsKeyT, tuple[float, int]], key: _TotalsKeyT, value: float
) -> None:
    """(합, 개수) 누적 한 벌 — 공종 계층과 기관 계층이 같은 식을 쓰게 한다."""
    total, count = totals.get(key, (0.0, 0))
    totals[key] = (total + value, count + 1)
