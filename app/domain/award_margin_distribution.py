"""낙찰자 하한 초과 마진 Δ = w − f 의 분포 커널 — 0 경계 안전 추정.

왜 이 모듈인가
--------------
낙찰은 "하한 이상 최저가 생존자"이므로, 낙찰자가 하한을 **얼마나 아슬아슬하게**
넘겼는지가 경쟁 정보의 전부다. 그 마진을 예정가-basis 로 정의하면::

    Δ = w − f = (낙찰가/예정가) − (게시 하한율)

사정률 ``a`` 가 소거되어 공고 크기·추첨 결과와 무관한 축이 된다(설계 §2). 이 축의
CDF ``G(δ)`` 하나가 win-proxy 합성(:mod:`app.domain.award_landing_distribution`)의
경쟁 항 전체를 담당한다.

Δ 는 **0 에서 절단**되고 0+ 에 스파이크(하한 밀착: 공사 중앙값 +55.5bp, 용역
+13.3bp, 실투찰 관측 +0.1bp)를 갖는다. 그래서 이 커널의 설계 제약은 하나로 모인다 —
**0 아래로 확률질량을 새게 하지 말 것**. 누출의 해악은
:class:`ReflectedMarginKde` docstring 이 두 독법으로 나눠 정확히 적는다.

순수 함수(I/O 0), stdlib 전용 — Phase 1 커널(:mod:`app.domain.reserve_draw_distribution`,
:mod:`app.domain.assessment_shrinkage`)과 같은 mypy strict 아일랜드다. 어느 행이 어느
셀에 속하는가(floor_bound 레짐 필터·공종×금액대 배정)는 호출부 책임이고, 이 커널은
집계된 관측만 받는다.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from math import erf, isfinite, sqrt
from statistics import pstdev, quantiles
from typing import Final, Protocol

from app.domain.assessment_shrinkage import pseudo_count_weight

# 음수 Δ 를 "표현 잡음"으로 볼 **기본** 허용폭. ``f``·``w`` 는 호출부에서 나눗셈으로
# 만들어지므로 하한에 정확히 착지한 개찰(w == f)이 −1e-16 규모의 음수 Δ 로 나올 수
# 있다. 그 크기만 0 으로 스냅하고 **별도로 계수**한다. 그보다 큰 음수는 조용히 clamp
# 하지 않고 거부한다 — "하한 아래 낙찰"은 도메인상 불가능하므로 basis 오염·era 불일치
# ·``award_floor_rate == 1.0`` 오적재의 신호이고(설계 §4 m1: 실측 Δ<0 4/2,152 건이
# 전부 f==1.0 오적재), clamp 하면 그 신호가 0+ 스파이크로 위장돼 사라진다.
#
# **상수가 아니라 기본값이다**(:func:`extract_margins` 의 ``negative_tolerance``).
# 입력 rate 의 양자화 오차(실측 rel-err ~1e-5)는 하한 밀착 마진(실측 최소 +0.1bp =
# 1e-5)을 음수로 밀 수 있고, 그렇게 거부되는 표본은 하필 **스파이크 최하단**이라 거부가
# 낙관 방향으로 작동한다. 값 재조정은 PR2 의 분포 증거를 보고 하며, 그 전까지는 보수적
# 으로 1e-12 를 유지한다 — 지금 넓히면 진짜 오염까지 함께 삼킨다.
NEGATIVE_MARGIN_EPSILON: Final[float] = 1e-12

# Silverman rule-of-thumb 대역폭: h = 0.9 · min(σ, IQR/1.349) · n^(−1/5).
SILVERMAN_FACTOR: Final[float] = 0.9
NORMAL_IQR_IN_SIGMA: Final[float] = 1.349
SILVERMAN_EXPONENT: Final[float] = -0.2

# 대역폭 하한. 마진은 rate 단위(1bp = 1e-4)이고 실측 착지는 하한 +0.1bp 까지 붙는다
# (실투찰 2건 +0.1bp/+1.9bp). 0.1bp 보다 넓게 평활하면 하한 밀착 스파이크 자체가
# 뭉개지므로 그 아래로 내려가지 않게 막는다. 표본이 전부 동률이면 σ=0 이라 이 하한이
# 실제로 쓰인다(대역폭 0 은 0 나눗셈이자 "확신"의 거짓 표현이다).
MIN_BANDWIDTH: Final[float] = 1e-5

# 표준정규 CDF 를 erf 로 환산할 때의 스케일: Φ(x) = ½(1 + erf(x/√2)).
SQRT_TWO: Final[float] = sqrt(2.0)


@dataclass(frozen=True)
class LandingObservation:
    """정산 개찰 1건의 낙찰자 착지 관측.

    두 basis 가 한 객체에 공존한다 — 섞으면 곧바로 오판이므로 필드와 프로퍼티로 축을
    못박는다:

    * ``floor_rate``(f)·``winning_rate``(w) 는 **예정가-basis**. Δ = w − f 는 사정률이
      소거돼 공고 간 전이가 가능하다.
    * ``realized_floor_rate``(φ = f·a)·``realized_winning_rate``(ω = w·a) 는
      **기초-basis**. 후보 ``b = P/B`` 와 같은 축이라 층 A 경험추정이 여기서 돈다.

    **ω 를 ``winning_amount/B`` 로 직접 받지 않고 ``w·a`` 로 유도하는 이유**(설계 §11-3
    은 basis 혼재 우회를 위해 직접 사용을 권한다): 층 A 는 ``φ ≤ b ≤ ω`` 를 한 개찰
    안에서 판정하므로 **φ 와 ω 가 같은 ``a`` 를 공유해야** 한다. 두 값을 독립 경로로
    받으면 ``a`` 가 미세하게 어긋난 φ·ω 쌍이 생겨 생존 구간이 실제와 달라진다.
    PR2 호출부의 계약은 그래서 "``a = E/B`` 와 ``w = winning_amount/E`` 를 각각 clean
    분모로 재계산해 넘긴다"이고, ``winning_amount/B`` 직접값은 그 곱과의 **대조용**
    으로만 쓴다(어긋나면 basis 사다리가 깨졌다는 신호다).
    """

    floor_rate: float
    winning_rate: float
    assessment_ratio: float

    @property
    def margin(self) -> float:
        """Δ = w − f (예정가-basis). 음수는 데이터 오류이며 추출기가 거부한다."""
        return self.winning_rate - self.floor_rate

    @property
    def realized_floor_rate(self) -> float:
        """φ = f·a — 실현 하한율(기초-basis)."""
        return self.floor_rate * self.assessment_ratio

    @property
    def realized_winning_rate(self) -> float:
        """ω = w·a — 낙찰자 투찰률(기초-basis)."""
        return self.winning_rate * self.assessment_ratio


@dataclass(frozen=True)
class MarginExtraction:
    """마진 추출 결과 — 채택 표본과 **거부 사유별 계수**를 함께 실어 나른다.

    거부를 조용히 버리면 "표본이 얕다"(시간이 해법)와 "표본이 오염됐다"(데이터가
    해법)가 구별되지 않는다. 셀 깊이 게이트(n≥150)를 판정하는 쪽이 두 수를 모두
    봐야 하므로 계수는 결과의 일부다.
    """

    margins: tuple[float, ...]
    negative_count: int
    invalid_count: int
    noise_snapped_count: int

    @property
    def accepted_count(self) -> int:
        return len(self.margins)

    @property
    def rejected_count(self) -> int:
        return self.negative_count + self.invalid_count


def extract_margins(
    observations: Sequence[LandingObservation],
    *,
    negative_tolerance: float = NEGATIVE_MARGIN_EPSILON,
) -> MarginExtraction:
    """관측 ``(f, w)`` → 오름차순 정렬된 Δ = w − f ≥ 0 표본.

    거부 규칙(조용한 보정 없음):

    * 비유한·비양수 rate → ``invalid_count``
    * Δ < −ε → ``negative_count`` (하한 아래 낙찰 = 데이터 오류)
    * −ε ≤ Δ < 0 → 0 으로 스냅하고 ``noise_snapped_count`` (부동소수 표현 잡음)

    ``negative_tolerance``(=ε)는 주입 가능하다 — 근거와 재조정 조건은
    :data:`NEGATIVE_MARGIN_EPSILON` 주석에 있다. 경계는 **닫혀 있다**: Δ 가 정확히
    ``−ε`` 면 거부가 아니라 스냅이다.
    """
    if not isfinite(negative_tolerance) or negative_tolerance < 0.0:
        raise ValueError("negative_tolerance must be a finite non-negative number")
    margins: list[float] = []
    negative_count = 0
    invalid_count = 0
    noise_snapped_count = 0
    for observation in observations:
        if not _is_valid_observation(observation):
            invalid_count += 1
            continue
        margin = observation.margin
        if margin < -negative_tolerance:
            negative_count += 1
            continue
        if margin < 0.0:
            noise_snapped_count += 1
            margin = 0.0
        margins.append(margin)
    return MarginExtraction(
        margins=tuple(sorted(margins)),
        negative_count=negative_count,
        invalid_count=invalid_count,
        noise_snapped_count=noise_snapped_count,
    )


def _is_valid_observation(observation: LandingObservation) -> bool:
    """세 rate 가 모두 유한 양수인가 — 0/음수/NaN 은 관측이 아니라 결측이다."""
    return all(
        isfinite(value) and value > 0.0
        for value in (
            observation.floor_rate,
            observation.winning_rate,
            observation.assessment_ratio,
        )
    )


class MarginDistribution(Protocol):
    """Δ 의 CDF 포트 — ECDF·경계보정 KDE·수축 혼합이 상호 대체 가능하다(§4.7-1).

    합성기는 이 계약만 알고 구현을 모른다. 그래서 백테스트는 ECDF, 서빙은 KDE,
    얕은 셀은 수축 혼합을 **같은 코드 경로**에 꽂는다.
    """

    def cumulative_probability(self, margin: float) -> float:
        """G(δ) = P(Δ ≤ δ)."""

    def survival_at_least(self, margin: float) -> float:
        """P(Δ ≥ δ) — **동률 포함**.

        합성 식의 이김 조건은 ``b ≤ ω ⟺ Δ ≥ b/a − f`` 로 경계를 포함한다(층 A 의
        ``φ_i ≤ b ≤ ω_i`` 와 같은 규칙). ``1 − G(δ)`` 는 P(Δ > δ) 라 원자가 있는
        ECDF 에서 층 A 와 갈리므로, 생존은 별도 계약으로 둔다.
        """


@dataclass(frozen=True)
class MarginEcdf:
    """경험 CDF — 기본 추정기(설계 §5.2). 단조·경계 안전·가정 최소.

    하한 0+ 스파이크를 평활 없이 그대로 표현하고, δ<0 에서 정확히 0 이다. 층 A 의
    접지 진리와 같은 계수 규칙을 쓰므로 두 층이 동일 데이터에서 정확히 일치한다.
    """

    sorted_margins: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.sorted_margins:
            raise ValueError("margin ECDF needs at least one sample")
        if any(
            later < earlier
            for earlier, later in zip(
                self.sorted_margins, self.sorted_margins[1:], strict=False
            )
        ):
            raise ValueError("margin ECDF samples must be sorted ascending")

    def cumulative_probability(self, margin: float) -> float:
        """G(δ) = (1/n)·#{Δ_i ≤ δ}."""
        return bisect_right(self.sorted_margins, margin) / len(self.sorted_margins)

    def survival_at_least(self, margin: float) -> float:
        """P(Δ ≥ δ) = (1/n)·#{Δ_i ≥ δ} — 동률을 생존 쪽에 넣는다."""
        below = bisect_left(self.sorted_margins, margin)
        return (len(self.sorted_margins) - below) / len(self.sorted_margins)


def build_margin_ecdf(margins: Sequence[float]) -> MarginEcdf:
    """마진 표본에서 ECDF 를 만든다(정렬은 여기서 책임진다)."""
    return MarginEcdf(sorted_margins=tuple(sorted(_validated_margins(margins))))


@dataclass(frozen=True)
class ReflectedMarginKde:
    """반사법(reflection) 경계보정 가우시안 KDE — 0 아래로 질량이 새지 않는다.

    0 에서 절단된 Δ 에 **소박한 가우시안 KDE** 를 씌우면 커널 질량의 일부가 δ<0 으로
    샌다. 그 누출이 어느 방향의 편향이 되는지는 **누출을 어떻게 처리하느냐**에 달렸고,
    두 독법 모두 틀린 답을 준다(설계 §5.2 의 "마진 과대 → 낙관"은 두 번째 독법에서만
    성립한다 — 리뷰 지적):

    * **누출을 방치**하면 ``Ĝ`` 는 [0,∞) 위 질량이 1 보다 작은 비정상 분포이고, CDF 가
      반사법보다 **일률적으로 크다**(누출분만큼). 생존이 과소평가돼 ``WW`` 가 작아지는
      **비관** 방향이다.
    * **누출을 잘라내고 재정규화**하면(정상 분포를 만드는 자연스러운 수리) 잘려나간
      질량이 하필 0+ **스파이크**라, 남은 확률이 큰 마진 쪽으로 재배분된다 → 마진 과대
      → 생존 과대 → **낙관** 방향.

    같은 표본으로 실측한 값이 이 서술을 고정한다(fixture: Δ=(0, 5, 10, 20, 50)bp,
    h=10bp, δ=5bp): 반사 0.2053 / 소박 방치 0.3134 / 소박 재정규화 0.1439.

    반사법은 각 표본을 −Δ_i 에도 한 번 더 놓아 0 에서 밀도를 접는다. 그러면 CDF 가
    닫힌 형태로 떨어진다::

        Ĝ(δ) = (1/n) Σ [ Φ((δ−Δ_i)/h) + Φ((δ+Δ_i)/h) ] − 1     (δ ≥ 0)

    Φ(−x) + Φ(x) = 1 이므로 **Ĝ(0) = 0 정확히**, Ĝ(∞) = 1 이다 — 누출 0 이 산술로
    증명되고, 값표 테스트가 이 등식과 **δ>0 의 구체값**을 함께 회귀 고정한다(0 경계만
    단언하면 이른 반환이 그 단언을 대신 통과시켜 반사항이 사라져도 안 깨진다).

    희소 셀·서빙 전이의 평활 전용이다. 표본이 충분하면 ECDF 가 기본이다. 평가 비용은
    표본 수에 비례하므로 곡선 전체를 반복 평가할 때는 :func:`tabulate_margin_cdf` 로
    한 번만 표를 만들어 쓴다.
    """

    margins: tuple[float, ...]
    bandwidth: float

    def __post_init__(self) -> None:
        if not self.margins:
            raise ValueError("reflected KDE needs at least one sample")
        if not isfinite(self.bandwidth) or self.bandwidth <= 0.0:
            raise ValueError("bandwidth must be a finite positive number")

    def cumulative_probability(self, margin: float) -> float:
        """반사 보정 CDF. δ ≤ 0 에서 정확히 0(경계 누출 없음)."""
        if margin <= 0.0:
            return 0.0
        folded = sum(
            _standard_normal_cdf((margin - sample) / self.bandwidth)
            + _standard_normal_cdf((margin + sample) / self.bandwidth)
            for sample in self.margins
        )
        return min(1.0, max(0.0, (folded / len(self.margins)) - 1.0))

    def survival_at_least(self, margin: float) -> float:
        """P(Δ ≥ δ) = 1 − Ĝ(δ) — 연속 분포라 원자가 없어 동률 규칙이 무의미하다."""
        return 1.0 - self.cumulative_probability(margin)


def build_reflected_kde(
    margins: Sequence[float], *, bandwidth: float | None = None
) -> ReflectedMarginKde:
    """경계보정 KDE 를 만든다. ``bandwidth`` 미지정 시 Silverman 경험식."""
    validated = _validated_margins(margins)
    resolved = silverman_bandwidth(validated) if bandwidth is None else bandwidth
    return ReflectedMarginKde(margins=tuple(validated), bandwidth=resolved)


def silverman_bandwidth(margins: Sequence[float]) -> float:
    """Silverman rule-of-thumb 대역폭(하한 :data:`MIN_BANDWIDTH`).

    IQR 항은 하한 밀착 스파이크 같은 두꺼운 꼬리·비정규성에서 σ 단독보다 안정적이라
    둘 중 작은 쪽을 쓴다. 표본이 1개거나 전부 동률이면 스케일이 0 이라 하한이 쓰인다.
    """
    count = len(margins)
    if count < 2:
        return MIN_BANDWIDTH
    # 관례는 표본표준편차(ddof=1)지만 여기선 모표준편차를 쓴다 — 더 작은 σ 는 더 좁은
    # 대역폭을 낳고, 좁은 대역폭은 0+ 스파이크를 덜 뭉갠다. 이 축에서 보수적인 방향은
    # "덜 평활"이다(과평활은 스파이크를 지워 마진을 과대평가한다).
    spread = pstdev(margins)
    quartiles = quantiles(margins, n=4, method="inclusive")
    iqr_scale = (quartiles[2] - quartiles[0]) / NORMAL_IQR_IN_SIGMA
    scale = min(spread, iqr_scale) if iqr_scale > 0.0 else spread
    # 표본 수 항 n^(−1/5). 음수 지수 거듭제곱은 타입상 Any 라 float 로 좁혀 둔다.
    sample_scaling: float = count**SILVERMAN_EXPONENT
    return max(SILVERMAN_FACTOR * scale * sample_scaling, MIN_BANDWIDTH)


@dataclass(frozen=True)
class TabulatedMarginCdf:
    """격자에 미리 계산한 CDF + 선형 보간 — 같은 분포의 O(1) 평가판.

    :class:`ReflectedMarginKde` 의 평가는 표본 수에 비례한다(O(n)). PR2 규모에서는
    곡선 한 개가 격자 × 지지집합 × 표본으로 커져 실측 34.7초가 걸렸다. 곡선은 같은
    분포를 수천 번 되묻는 작업이므로, **한 번 표를 만들고 그 뒤로는 보간**한다.

    보간은 CDF 위에서 한다 — 격자 값이 단조 비감소면 보간값도 단조 비감소이고,
    ``grid[0] <= 0`` 이면 경계 안전(δ<0 에서 0)도 그대로 보존된다. 격자 밖은 양 끝값을
    유지한다(왼쪽=0, 오른쪽=포화).

    **연속 분포(KDE) 전용**이다. ECDF 를 표로 만들면 계단의 수직 구간이 사선으로
    뭉개져 원자가 사라지므로, 그때는 표를 쓰지 말고 ECDF 를 그대로 쓴다(ECDF 는 이미
    O(log n) 이라 표가 필요 없다).
    """

    grid: tuple[float, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.grid) < 2:
            raise ValueError("tabulated CDF needs at least two grid points")
        if len(self.grid) != len(self.values):
            raise ValueError("tabulated grid and values must align")
        if any(
            later <= earlier
            for earlier, later in zip(self.grid, self.grid[1:], strict=False)
        ):
            raise ValueError("tabulated grid must be strictly ascending")

    def cumulative_probability(self, margin: float) -> float:
        """격자 위 선형 보간 CDF."""
        if margin <= self.grid[0]:
            return self.values[0]
        if margin >= self.grid[-1]:
            return self.values[-1]
        upper = bisect_left(self.grid, margin)
        lower = upper - 1
        span = self.grid[upper] - self.grid[lower]
        fraction = (margin - self.grid[lower]) / span
        return self.values[lower] + (
            fraction * (self.values[upper] - self.values[lower])
        )

    def survival_at_least(self, margin: float) -> float:
        """P(Δ ≥ δ) = 1 − Ĝ(δ) — 원본이 연속이라 원자가 없다."""
        return 1.0 - self.cumulative_probability(margin)


def tabulate_margin_cdf(
    source: MarginDistribution, grid: Sequence[float]
) -> TabulatedMarginCdf:
    """``source`` 의 CDF 를 ``grid`` 에 한 번 계산해 :class:`TabulatedMarginCdf` 로."""
    return TabulatedMarginCdf(
        grid=tuple(float(point) for point in grid),
        values=tuple(source.cumulative_probability(point) for point in grid),
    )


@dataclass(frozen=True)
class ShrunkMarginDistribution:
    """셀 분포와 부모(공종→전역) 분포의 pseudo-count 혼합.

    **밀도가 아니라 CDF 를 섞는다**: 혼합분포의 CDF 는 성분 CDF 의 볼록결합이므로
    단조·[0,1]·경계 안전(δ<0 에서 0)이 재정규화 없이 자동 보존된다. 성분이 둘 다
    이 모듈의 추정기면 0 누출도 그대로 0 이다.

    자기 자신도 :class:`MarginDistribution` 계약을 만족하므로 셀→공종→전역 다단
    수축이 중첩으로 표현된다.
    """

    cell: MarginDistribution
    parent: MarginDistribution
    cell_weight: float

    def cumulative_probability(self, margin: float) -> float:
        return _blend(
            self.cell_weight,
            self.cell.cumulative_probability(margin),
            self.parent.cumulative_probability(margin),
        )

    def survival_at_least(self, margin: float) -> float:
        return _blend(
            self.cell_weight,
            self.cell.survival_at_least(margin),
            self.parent.survival_at_least(margin),
        )


def shrink_margin_distribution(
    *,
    cell: MarginDistribution,
    parent: MarginDistribution,
    cell_sample_count: int,
    prior_strength: float,
) -> ShrunkMarginDistribution:
    """``n/(n+κ)`` 가중으로 셀 마진 분포를 부모 분포로 수축한다.

    κ(``prior_strength``)는 **주입 파라미터**다 — 이 커널은 값을 모른다. 마진 축의 κ 는
    사정률 축의 κ(=12)와 다른 축이고, 상수로 박으면 사후 산물이 된다(설계 §5.2:
    "κ 값은 릴리스 단위 재추정"). 재추정 책임은 호출부(릴리스 파이프라인)에 있다.
    """
    if cell_sample_count < 0:
        raise ValueError("cell_sample_count must not be negative")
    if not isfinite(prior_strength) or prior_strength <= 0.0:
        raise ValueError("prior_strength must be a finite positive number")
    return ShrunkMarginDistribution(
        cell=cell,
        parent=parent,
        # 수축 강도 n/(n+κ) 의 단일 출처(§4.5-8). 저쪽은 평균을, 여기서는 CDF 를
        # 섞지만 "표본이 κ 만큼 쌓여야 자기 주장이 절반"이라는 규칙은 하나다.
        cell_weight=pseudo_count_weight(cell_sample_count, prior_strength),
    )


def _blend(weight: float, cell_value: float, parent_value: float) -> float:
    return (weight * cell_value) + ((1.0 - weight) * parent_value)


def _standard_normal_cdf(value: float) -> float:
    """Φ(x) = ½(1 + erf(x/√2)) — stdlib 만으로 얻는 표준정규 CDF."""
    return 0.5 * (1.0 + erf(value / SQRT_TWO))


def _validated_margins(margins: Sequence[float]) -> list[float]:
    """추정기 입력 검증 — 유한·비음수만 허용(추출기를 통과한 표본을 기대한다)."""
    validated = [float(margin) for margin in margins]
    if any(not isfinite(margin) or margin < 0.0 for margin in validated):
        raise ValueError("margins must be finite non-negative numbers")
    if not validated:
        raise ValueError("margin estimator needs at least one sample")
    return validated
