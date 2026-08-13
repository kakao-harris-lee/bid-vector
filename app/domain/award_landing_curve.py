"""win-proxy 곡선과 착지점 판정 — 격자·argmax·실격 예산 제약·불안정성 진단.

왜 별도 모듈인가
----------------
:mod:`app.domain.award_landing_distribution` 은 "점 ``b`` 에서 얼마인가"(추정)를,
이 모듈은 "그래서 어디에 설 것인가"(판정)를 담당한다. 판정에는 추정에 없는 것이
붙는다 — 격자, 동률 처리 규칙, **실격 예산 제약**, 그리고 판정이 얼마나 임의적인지의
진단. 두 축을 한 파일에 두면 500줄 한도(§4.5-4)를 넘기도 한다.

핵심은 **소박한 argmax 를 쓰지 않는다**는 것이다(설계 §7 M2). ``WW`` 는 실격(하한
미달)과 패찰을 등가로 취급하지만 운영자는 아니다 — 실측으로 소박한 ``argmax WW_emp``
지점의 실격확률은 공사 **32.5%**(argmax 0.89775, n=1,405) · 용역 **52.6%**(argmax
0.87825, n=743) 였다. 그래서 판정은::

    b* = argmax_{b : P(φ > b) ≤ α} WW(b)

이고, ``α`` 는 **주입**한다 — 사후 튜닝을 막으려면 데이터를 보고 정하는 게 아니라
릴리스 단위로 선언해야 한다(κ 와 같은 사유, `award_rate_windows` 의
``GATE_MIN_EVALUATION_ROWS`` 교훈).

``P(φ>b)`` 는 ``b`` 에 **비증가**이므로(높이 쓸수록 하한 미달 위험이 준다) 실행가능
집합은 상향폐집합 ``[b_min(α), ∞)`` 이고, **α-제약은 착지점을 위로 민다** — 하한을
하나 더 얹는 것과 같다. "보수적 = 낮은 가격"이라는 통상 직관과 반대 방향이다.

판정이 성립하지 않는 두 경우는 조용한 fallback 없이 :class:`ConstrainedOptimumStatus`
로 구분해 돌려준다(α 만족 격자점 0 / 만족하지만 ``WW`` 전부 0).

분할 이력과 다음 확장의 자리(seam)
----------------------------------
* **곡선 빌더는 이미 떼어냈다** → :mod:`app.domain.award_landing_curve_builders`.
  비단조 검증·seam 선언이 붙으며 이 파일이 500줄(§4.5-4)을 넘어, 미리 선언해 둔
  seam 을 그대로 실행했다. 세 빌더는 점 추정량을 격자에 대고 도는 **순수 조립**일
  뿐 판정·진단과 상태를 공유하지 않았다. 그 결과 이 파일은 **점 추정량에 전혀
  의존하지 않는 곡선 대수**가 됐다 — 어떤 곡선이 들어와도 같은 규칙으로 판정한다.
* **EV 층(옵션 B)은 이 파일을 늘리는 게 아니라 별도 모듈**(``award_landing_ev.py``)로
  간다. ``EV(b) = WW(b)·(b−c)·B`` 는 **목적함수 교체**이지 격자·동률·진단 기계의
  확장이 아니다 — 여기에 섞으면 "무엇을 최대화하는가"가 파일 안에 둘이 된다.

순수 함수(I/O 0), stdlib 전용 — Phase 1 커널과 같은 mypy strict 아일랜드.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Final

# 후보 격자 기본 해상도. 실측 착지는 하한 +0.1bp 까지 붙으므로(실투찰 2건 +0.1bp/
# +1.9bp) 1bp(1e-4) 격자는 결정적 구간을 통째로 건너뛴다. 0.1bp = 1e-5.
DEFAULT_GRID_STEP: Final[float] = 1e-5
# 격자 점 수 상한. 곡선 1개의 비용이 O(격자 × 지지집합)이라 폭주를 조용히 허용하지
# 않고 명시적으로 거부한다(0.1bp 격자로 20%p 폭 = 하한 밴드를 충분히 덮는다).
MAX_GRID_POINTS: Final[int] = 20_001
# 동률 판정 허용오차. WW 는 확률이고 셀 표본 N 이 수백~수천이면 해상도가 1/N ≳ 1e-4
# 이므로, 1e-9 차이는 봉우리가 아니라 부동소수 잡음이다.
WIN_PROXY_TIE_TOLERANCE: Final[float] = 1e-9
# 실격확률 비증가 검증의 허용오차. **엄격 비교를 쓰면 안 된다**: 합성 경로의
# ``P(φ>b)`` 는 지지집합의 **부분합**이라, b 가 커져 항이 빠져도 서로 다른 덧셈 순서
# 때문에 1 ulp 역전이 이론상 가능하다. 누적 오차 규모는 지지집합 크기(Phase 1 완전
# 열거 최대 1,365 원자) × 배정밀도 eps ≈ 3e-13 이고, 진짜 위반은 경험 경로에서
# 1/N ≳ 1e-4, 합성 경로에서 원자 1개의 확률질량 규모다. 1e-9 는 두 규모 사이에
# 양쪽으로 3~4 자릿수 여유를 두고 앉는다.
DISQUALIFICATION_MONOTONICITY_TOLERANCE: Final[float] = 1e-9


class PlateauChoice(Enum):
    """동률 최고점 구간에서 어느 끝을 ``b*`` 로 삼을지(§4.5-2 선언적 선택).

    기본은 :attr:`UPPER` 다 — 같은 ``WW`` 구간에서는 높은 ``b`` 가 **약우위(weakly
    dominant)** 이기 때문이다: ``P(생존)`` 은 ``b`` 에 비감소라 실격확률이 상단에서
    약소하고, 낙찰 시 수취액도 상단이 크다(EV 층도 같은 방향). 낮은 쪽을 고를 이유는
    "곡선이 떨어지기 직전이라 잡음에 취약하다"인데, 그 논거는 구간 하단에도 대칭으로
    성립하므로 선택 근거가 되지 못한다.

    구간 자체는 :class:`CurveDiagnostics` 가 양 끝을 그대로 공시하므로, 다른 규칙이
    필요한 소비자는 곡선 위에서 직접 고르면 된다.
    """

    LOWER = "lower"
    UPPER = "upper"


@dataclass(frozen=True)
class CurveDiagnostics:
    """``argmax`` 판정과 그 **불안정성 증거**를 한 값으로 묶는다.

    argmax 한 점만 보고하면 곡선이 평탄하거나 다봉일 때 ``b*`` 가 표본 잡음으로
    흔들리는 것을 숨기게 된다(설계 §11-7). "이겼다"와 "쟀다"를 구별하려면 판정 옆에
    동률 구간 폭·봉우리 수·곡선 진폭이 함께 있어야 한다.
    """

    optimal_bid_rate: float
    peak_value: float
    plateau_lower_rate: float
    plateau_upper_rate: float
    mode_count: int
    value_range: float
    tie_tolerance: float

    @property
    def plateau_width(self) -> float:
        """동률 최고점 구간의 폭 — 넓을수록 ``b*`` 선택이 임의적이다."""
        return self.plateau_upper_rate - self.plateau_lower_rate

    @property
    def is_flat(self) -> bool:
        """곡선 진폭이 허용오차 이하 = ``b`` 가 승률을 못 움직인다(판정 불가)."""
        return self.value_range <= self.tie_tolerance

    @property
    def is_multimodal(self) -> bool:
        """봉우리가 둘 이상 = 단일 ``b*`` 보고가 곡선을 대표하지 못한다."""
        return self.mode_count > 1


class ConstrainedOptimumStatus(Enum):
    """제약 판정의 결말 — 두 실패를 한 이름으로 뭉개지 않는다(§4.5-2).

    소비자의 다음 행동이 다르기 때문이다: ``BUDGET_INFEASIBLE`` 은 격자를 넓히거나
    α 를 재선언해야 하고, ``DEGENERATE_WIN_PROXY`` 는 그 셀에서 이길 방법이 애초에
    없다는 뜻이라 격자를 넓혀도 소용없다.
    """

    OPTIMAL = "optimal"
    # α 를 만족하는 격자점이 하나도 없음.
    BUDGET_INFEASIBLE = "budget_infeasible"
    # α 는 만족하지만 그 구간의 ``WW`` 가 전부 0 — 이길 수 있는 후보가 없다.
    DEGENERATE_WIN_PROXY = "degenerate_win_proxy"


@dataclass(frozen=True)
class ConstrainedOptimum:
    """실격 예산 ``α`` 제약 하의 착지점 — **불능도 결과의 일부**다.

    제약을 만족하는 격자점이 없을 때 조용히 소박한 argmax 로 되돌아가면, 실격을
    감수하겠다는 결정이 코드 안에서 일어난다. 그래서 실패는 :class:`
    ConstrainedOptimumStatus` 로 명시하고 ``optimal_bid_rate`` 는 ``None`` 이 된다 —
    소비자가 판정을 건너뛸지 격자를 넓힐지 α 를 재선언할지 결정해야 한다.

    ``feasible_diagnostics`` 는 **실행가능 집합만**을 본 진단이다. 전 격자를 보는
    :meth:`WinProxyCurve.diagnostics` 와 값이 다를 수 있고(제약이 봉우리를 잘라내면
    ``mode_count`` 가 줄고 ``value_range`` 도 좁아진다), 다른 게 정상이다. 제약 판정
    옆에 무제약 진단을 갖다 붙이면 "이 b* 는 평탄한 곡선 위의 임의 선택"을
    실제보다 낙관적으로 읽게 되므로, 짝이 맞는 진단을 **결과에 실어** 잘못된 짝
    맞추기를 구조로 막는다.
    """

    alpha: float
    status: ConstrainedOptimumStatus
    feasible_point_count: int
    disqualification_probability: float | None
    feasible_diagnostics: CurveDiagnostics | None

    @property
    def is_feasible(self) -> bool:
        """판정이 성립했는가 — 두 실패 모두 ``False`` 다."""
        return self.status is ConstrainedOptimumStatus.OPTIMAL

    @property
    def optimal_bid_rate(self) -> float | None:
        return None if self.feasible_diagnostics is None else (
            self.feasible_diagnostics.optimal_bid_rate
        )

    @property
    def peak_value(self) -> float | None:
        return None if self.feasible_diagnostics is None else (
            self.feasible_diagnostics.peak_value
        )

    @property
    def plateau_lower_rate(self) -> float | None:
        return None if self.feasible_diagnostics is None else (
            self.feasible_diagnostics.plateau_lower_rate
        )

    @property
    def plateau_upper_rate(self) -> float | None:
        return None if self.feasible_diagnostics is None else (
            self.feasible_diagnostics.plateau_upper_rate
        )


@dataclass(frozen=True)
class WinProxyCurve:
    """격자 위 ``WW`` 곡선(선택적으로 실격확률 곡선 동반).

    ``b*`` 의 동률 규칙은 :class:`PlateauChoice` 주입으로 정하고 기본은 상단이다.

    **실격확률 곡선은 비증가여야 한다**(계약). ``P(φ>b) = P(a > b/f)`` 는 도메인
    정의상 ``b`` 에 비증가이고 세 곡선 빌더가 전부 구조적으로 만족하므로 이는 새 제약이
    아니라 명문화다. 검증이 **필요한** 이유는 :meth:`constrained_optimum` 이 실행가능
    점만 남긴 곡선 위에서 판정하기 때문이다 — 실행가능 점들이 서로 이웃이 되므로,
    비단조 입력이면 동률 구간이 원 격자의 골짜기를 가로질러 "제약 밖 좌표를 포함하지
    않는다"는 보장이 깨진다. 계약을 입구에서 막아 그 보장을 다시 구조적으로 참으로
    만든다.
    """

    bid_rates: tuple[float, ...]
    values: tuple[float, ...]
    disqualification_probabilities: tuple[float, ...] | None = None
    tie_tolerance: float = WIN_PROXY_TIE_TOLERANCE
    plateau_choice: PlateauChoice = PlateauChoice.UPPER

    def __post_init__(self) -> None:
        if not self.bid_rates:
            raise ValueError("win proxy curve needs at least one grid point")
        if len(self.bid_rates) != len(self.values):
            raise ValueError("curve grid and values must align")
        if self.tie_tolerance < 0.0:
            raise ValueError("tie_tolerance must not be negative")
        if self.disqualification_probabilities is None:
            return
        if len(self.disqualification_probabilities) != len(self.bid_rates):
            raise ValueError("disqualification curve must align with the grid")
        if any(
            later > earlier + DISQUALIFICATION_MONOTONICITY_TOLERANCE
            for earlier, later in zip(
                self.disqualification_probabilities,
                self.disqualification_probabilities[1:],
                strict=False,
            )
        ):
            raise ValueError(
                "disqualification probabilities must be non-increasing in the bid rate"
            )

    @property
    def peak_value(self) -> float:
        return max(self.values)

    @property
    def optimal_bid_rate(self) -> float:
        """제약 없는 ``argmax_b WW(b)``. 판정에는 :meth:`constrained_optimum` 을 쓴다."""
        return self.diagnostics().optimal_bid_rate

    def diagnostics(self) -> CurveDiagnostics:
        """판정 + 평탄/다봉 진단을 한 번의 순회로 계산한다."""
        return self._resolve()[1]

    def _resolve(self) -> tuple[int, CurveDiagnostics]:
        """고른 격자 **인덱스**와 진단을 함께 돌려준다(제약 경로가 인덱스를 쓴다)."""
        peak = max(self.values)
        threshold = peak - self.tie_tolerance
        chosen = _choose_index(
            _near_peak_indices(self.values, threshold), self.plateau_choice
        )
        lower_index, upper_index = _contiguous_run(self.values, chosen, threshold)
        return chosen, CurveDiagnostics(
            optimal_bid_rate=self.bid_rates[chosen],
            peak_value=peak,
            plateau_lower_rate=self.bid_rates[lower_index],
            plateau_upper_rate=self.bid_rates[upper_index],
            mode_count=_count_modes(self.values, self.tie_tolerance),
            value_range=peak - min(self.values),
            tie_tolerance=self.tie_tolerance,
        )

    def constrained_optimum(self, *, alpha: float) -> ConstrainedOptimum:
        """``b* = argmax_{b: P(φ>b) ≤ α} WW(b)`` — 실격 예산 제약 판정(설계 §7).

        ``alpha`` 는 기본값 없이 주입한다(사후 튜닝 방지). 실격확률 곡선이 없는
        커브에는 적용할 수 없으므로 명시적으로 실패시킨다 — "제약이 없었다"와
        "제약을 만족했다"가 같은 결과로 보이면 안 된다.

        판정은 **실행가능 집합으로 잘라낸 곡선 위에서** 그대로 한 번 더 돈다. 그래서
        동률 구간도 진단도 제약 밖 좌표를 절대 포함하지 않고, 반환값의 진단은 판정과
        같은 스코프임이 구조로 보장된다.
        """
        if not isfinite(alpha) or not (0.0 <= alpha <= 1.0):
            raise ValueError("alpha must be a probability in [0, 1]")
        budget = self.disqualification_probabilities
        if budget is None:
            raise ValueError(
                "constrained optimum needs a disqualification probability curve"
            )
        feasible = [
            index
            for index, probability in enumerate(budget)
            if probability <= alpha
        ]
        if not feasible:
            return _failed_optimum(
                alpha, ConstrainedOptimumStatus.BUDGET_INFEASIBLE, 0
            )
        chosen, diagnostics = self._restricted_to(feasible)._resolve()
        if diagnostics.peak_value <= 0.0:
            # 실행가능 구간의 WW 가 전부 0 이면 "최고점"은 판정이 아니라 격자 위치의
            # 부산물이다(동률 규칙이 UPPER 면 밴드 끝, LOWER 면 반대 끝 — 비대칭
            # 실패모드). 조용히 돌려주면 실격을 피하려다 이길 수 없는 가격을 고르게
            # 되므로, 격자 밴드 오설정을 삼키지 않고 실패로 둔다.
            return _failed_optimum(
                alpha,
                ConstrainedOptimumStatus.DEGENERATE_WIN_PROXY,
                len(feasible),
            )
        return ConstrainedOptimum(
            alpha=alpha,
            status=ConstrainedOptimumStatus.OPTIMAL,
            feasible_point_count=len(feasible),
            disqualification_probability=budget[feasible[chosen]],
            feasible_diagnostics=diagnostics,
        )

    def _restricted_to(self, indices: Sequence[int]) -> WinProxyCurve:
        """선택된 격자점만 남긴 같은 규칙의 곡선(동률 규칙·허용오차 유지)."""
        return WinProxyCurve(
            bid_rates=tuple(self.bid_rates[index] for index in indices),
            values=tuple(self.values[index] for index in indices),
            disqualification_probabilities=(
                None
                if self.disqualification_probabilities is None
                else tuple(
                    self.disqualification_probabilities[index] for index in indices
                )
            ),
            tie_tolerance=self.tie_tolerance,
            plateau_choice=self.plateau_choice,
        )


def build_bid_rate_grid(
    lower_rate: float, upper_rate: float, *, step: float = DEFAULT_GRID_STEP
) -> tuple[float, ...]:
    """``[lower, upper]`` 구간의 균등 후보 격자. 인덱스 곱으로 누적오차를 막는다."""
    if not (isfinite(lower_rate) and isfinite(upper_rate)):
        raise ValueError("grid bounds must be finite")
    if upper_rate < lower_rate:
        raise ValueError("upper_rate must not be below lower_rate")
    if not isfinite(step) or step <= 0.0:
        raise ValueError("step must be a finite positive number")
    point_count = int((upper_rate - lower_rate) / step) + 1
    if point_count > MAX_GRID_POINTS:
        raise ValueError(
            f"grid would need {point_count} points (max {MAX_GRID_POINTS})"
        )
    return tuple(lower_rate + (index * step) for index in range(point_count))


def _near_peak_indices(values: Sequence[float], threshold: float) -> list[int]:
    """최고점과 허용오차 안에서 동률인 격자점 — **연속일 필요는 없다**.

    골짜기 건너편의 같은 높이 봉우리도 여기 들어온다. 약우위 논증은 "같은 ``WW``면
    높은 ``b``"이지 "같은 봉우리 안에서"가 아니므로, 선택은 이 전체 집합에서 한다.
    """
    return [index for index, value in enumerate(values) if value >= threshold]


def _choose_index(indices: Sequence[int], plateau_choice: PlateauChoice) -> int:
    """동률 집합에서 선언된 규칙으로 한 점을 고른다(§4.5-2)."""
    return indices[0] if plateau_choice is PlateauChoice.LOWER else indices[-1]


def _failed_optimum(
    alpha: float, status: ConstrainedOptimumStatus, feasible_point_count: int
) -> ConstrainedOptimum:
    """판정 실패 — 좌표도 진단도 남기지 않는다(부분 결과가 곧 조용한 fallback 이다)."""
    return ConstrainedOptimum(
        alpha=alpha,
        status=status,
        feasible_point_count=feasible_point_count,
        disqualification_probability=None,
        feasible_diagnostics=None,
    )


def _contiguous_run(
    values: Sequence[float], index: int, threshold: float
) -> tuple[int, int]:
    """``index`` 를 품은 **연속** 동률 구간의 (시작, 끝).

    :func:`_tie_runs` 의 "동률"과 의미가 다르다: 저쪽은 *이웃 값끼리* 구간 시작 대비
    허용오차 안이면 묶어 곡선을 압축하고(봉우리 세기용), 이쪽은 *최고점 대비*
    허용오차 안인 점만 이어붙인다(구간 폭 공시용). 그래서 같은 곡선에서 두 함수가
    내는 구간이 다를 수 있고, 그게 정상이다.

    제약 판정은 실행가능 집합으로 **잘라낸 곡선**에 이 함수를 적용하므로, 여기서
    별도로 실행가능 여부를 볼 필요가 없다(잘라낸 뒤에는 전부 실행가능하다).
    """
    lower = index
    while lower > 0 and values[lower - 1] >= threshold:
        lower -= 1
    upper = index
    while upper + 1 < len(values) and values[upper + 1] >= threshold:
        upper += 1
    return lower, upper


def _tie_runs(values: Sequence[float], tolerance: float) -> list[int]:
    """동률(허용오차 내) 연속 구간의 **시작 인덱스** 목록으로 곡선을 압축한다.

    비교 기준은 직전 원소가 아니라 구간 시작 값이다 — 직전 비교로는 허용오차보다
    작은 계단이 무한히 이어져 서로 다른 높이가 한 구간으로 뭉친다.
    """
    starts = [0]
    for index in range(1, len(values)):
        if abs(values[index] - values[starts[-1]]) > tolerance:
            starts.append(index)
    return starts


def _count_modes(values: Sequence[float], tolerance: float) -> int:
    """국소 최대 구간의 개수 — 어느 이웃 구간도 자기보다 높지 않은 구간을 센다."""
    starts = _tie_runs(values, tolerance)
    modes = 0
    for position, start in enumerate(starts):
        level = values[start]
        higher_before = position > 0 and values[starts[position - 1]] > level + tolerance
        higher_after = (
            position + 1 < len(starts)
            and values[starts[position + 1]] > level + tolerance
        )
        if not (higher_before or higher_after):
            modes += 1
    return modes
