"""승격 게이트의 **평가 창 정책** — 어떤 표본 위에서 재는가 (Phase 2c).

무엇이 바뀌었고 왜인가
----------------------
이전 게이트의 rolling origin 은 평가 층 행 수의 **비율**(``train_fraction``)이었다. 비율
분할은 행이 시간에 고르게 퍼져 있을 때만 서로 다른 시점을 만드는데, 이 코퍼스는 83% 가 3주에
몰려 있어 다섯 fraction 이 같은 5일 구간으로 붕괴했다(실측 2026-08-13: ``frac=0.60`` 의
홀드아웃이 ``frac=0.80`` 의 홀드아웃 1,046행을 **100% 포함**, 나머지 쌍도 전부 100%). 즉
"모든 origin 에서 통과"는 시간에 걸친 안정성이 아니라 **같은 1,046행이 다섯 번 통과했다**는
뜻이었다.

그래서 origin 을 **겹치지 않는 날짜 구간**으로 바꾼다. 창은 하드코딩된 날짜가 아니라
데이터에서 도출한다: 정산 성숙도(:mod:`app.domain.settlement_maturity`)가 임계 이상인 KST
주가 곧 평가 창이다.

임계는 손잡이가 아니다
----------------------
:data:`GATE_MATURITY_THRESHOLD` 는 "측정할 수 없는 선택편향을 얼마나 허용하는가"의 **선언**
이다. 통과를 만들려고 낮추면 이 모듈이 존재할 이유가 사라지므로, 값 변경은 근거와 함께
여기 한 곳에서 한다(CLI 플래그를 두지 않는 이유도 같다).

판정식은 이 모듈이 건드리지 않는다. 비교 베이스라인·대응 t 임계·평가 층은
:mod:`app.services.ml_training.award_rate_holdout` 의 선언 그대로다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.domain.settlement_maturity import MaturityWindow
from app.schemas._base import StrictModel
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow

__all__ = [
    "EXCLUDED_BEYOND_MAX_ORIGINS",
    "EXCLUDED_IMMATURE",
    "EXCLUDED_NO_TRAINING_ROWS",
    "EXCLUDED_TOO_FEW_ROWS",
    "GATE_MATURITY_THRESHOLD",
    "GATE_MAX_ORIGINS",
    "GATE_MIN_EVALUATION_ROWS",
    "GATE_STRATUM",
    "EvaluationPlan",
    "HoldoutOverlap",
    "HoldoutOverlapSummary",
    "WindowExclusion",
    "WindowPolicy",
    "WindowSummary",
    "holdout_overlaps",
    "indices_in_window",
    "plan_evaluation_windows",
    "rows_in_window",
    "summarize_exclusions",
    "summarize_overlaps",
    "summarize_window",
]

# 평가 층. 서빙이 마주하는 축(관측된 기초금액)이며, 사전 선언된 베이스라인 표도 이 층에서
# 측정됐다. 창 선택과 홀드아웃 분할이 같은 층을 봐야 하므로 선언은 여기 한 곳이다.
GATE_STRATUM: Final[str] = "clean-base"

# embargo 임계 — 평가 창의 정산 비율이 이 값 미만이면 그 창에서는 재지 않는다.
#
# 0.70 인 근거: 이 코퍼스에서 성숙도는 시간이 아니라 수집 커버리지의 함수라 구간별로
# 0.42~0.82 로 흩어져 있고(실측 2026-08-13), 0.70 은 "3건 중 2건 이상은 결과를 안다"에
# 해당한다. 남은 30% 의 선택 방향은 스키마상 측정 불가이므로(도메인 모듈 docstring) 이
# 값은 정확도가 아니라 **감수하는 무지의 상한**을 선언한 것이다.
GATE_MATURITY_THRESHOLD: Final[float] = 0.70

# 평가 창 하나가 게이트에 오르기 위한 최소 홀드아웃 행 수.
#
# 이 값이 보장하는 것과 보장하지 않는 것 (실측으로 정정)
# ------------------------------------------------------
# 처음에는 "대응 t 의 정규근사가 성립하는 최소 표본"으로 선언했다. 그 근거는 반증됐다:
# 145행 창은 정규근사에 넉넉한데도 seed 를 바꾸면 개선률이 +7.12% ~ −3.77% 로 **부호가
# 뒤집힌다**(실측 2026-08-13, seed 5종). 이 값이 막는 것은 통계량의 성립 불가이지
# **검정력 부족이 아니다**.
#
# 그렇다면 검정력 기준으로 올리지 않는 이유. 필요 표본은 행당 효과 크기에 달렸고 그 크기는
# 창마다 다르며 **재기 전에는 모른다**. 관측 효과에서 역산한 수(이 코퍼스 n≈545)를 상수로
# 굳히면 그 정책은 이 데이터의 사후 산물이 되고, 하필 그 값은 통과한 창(357행)까지 함께
# 지운다 — 어느 방향이든 결과를 보고 정한 값이다.
#
# 그래서 이 상수는 **하한만** 선언한다: 이보다 얕은 창은 잰 것으로 취급하지 않는다(22행
# 창의 t 는 판정으로 읽을 값이 아니다). 창별 검정력은 상수가 아니라 **리포트가 창마다**
# 말한다 — 검출 가능했던 최소 개선(MDE)·필요 표본 수·seed 안정성
# (:mod:`app.services.ml_training.award_rate_diagnostics`). 판정 옆에 그 세 수가 없으면
# "못 이겼다"와 "못 쟀다"가 구별되지 않고, 이 트랙은 그 구별을 위해 존재한다.
GATE_MIN_EVALUATION_ROWS: Final[int] = 100

# 평가할 창의 최대 개수(가장 최근 것부터). 서빙에 가까운 구간이 더 많은 정보를 준다.
GATE_MAX_ORIGINS: Final[int] = 5

# 제외 사유 어휘(§4.5-3 선언). 리포트와 판정이 같은 문자열을 본다.
EXCLUDED_IMMATURE: Final[str] = "immature"
EXCLUDED_TOO_FEW_ROWS: Final[str] = "insufficient-evaluation-rows"
EXCLUDED_NO_TRAINING_ROWS: Final[str] = "no-training-rows"
EXCLUDED_BEYOND_MAX_ORIGINS: Final[str] = "beyond-max-origins"


@dataclass(frozen=True)
class WindowPolicy:
    """평가 창 선택 정책 한 벌 — 기본값이 선언된 게이트다(주입 seam)."""

    maturity_threshold: float = GATE_MATURITY_THRESHOLD
    min_evaluation_rows: int = GATE_MIN_EVALUATION_ROWS
    max_origins: int = GATE_MAX_ORIGINS


@dataclass(frozen=True)
class _WindowFacts:
    """한 창에 대한 관측 — 규칙 표가 보는 것은 이것뿐이다."""

    window: MaturityWindow
    evaluation_row_count: int
    train_row_count: int


@dataclass(frozen=True)
class WindowExclusion:
    """제외된 창 하나와 그 사유. 제외는 **리포트에 남는 사실**이지 침묵이 아니다."""

    window: MaturityWindow
    reason: str
    evaluation_row_count: int


@dataclass(frozen=True)
class EvaluationPlan:
    """평가에 쓰는 창과 뺀 창 한 벌."""

    selected: tuple[MaturityWindow, ...]
    excluded: tuple[WindowExclusion, ...]
    policy: WindowPolicy


@dataclass(frozen=True)
class HoldoutOverlap:
    """두 창의 홀드아웃이 공유하는 행 수. 설계상 0이어야 하고, 0이 아니면 드러나야 한다."""

    first_start: datetime
    second_start: datetime
    row_count: int


# 제외 규칙 표. 새 사유는 코드 분기가 아니라 여기 한 줄이고, **먼저 걸리는 사유가 기록된다**
# (성숙도 미달이면서 표본도 적은 창은 "immature" 로 남는다 — 근본 사유가 앞이다).
_EXCLUSION_RULES: Final[
    tuple[tuple[str, Callable[[_WindowFacts, WindowPolicy], bool]], ...]
] = (
    (
        EXCLUDED_IMMATURE,
        lambda facts, policy: facts.window.maturity < policy.maturity_threshold,
    ),
    (
        EXCLUDED_TOO_FEW_ROWS,
        lambda facts, policy: facts.evaluation_row_count < policy.min_evaluation_rows,
    ),
    (EXCLUDED_NO_TRAINING_ROWS, lambda facts, _policy: facts.train_row_count <= 0),
)


def indices_in_window(
    rows: Sequence[AwardRateTrainingRow],
    window: MaturityWindow,
    *,
    stratum: str = GATE_STRATUM,
) -> list[int]:
    """창 ``[start, end)`` 안의 평가 층 행 **위치** — 홀드아웃 선택 규칙의 단일 출처.

    홀드아웃 분할과 겹침 회계가 같은 규칙을 쓴다. 두 곳이 규칙을 각자 쓰면 "겹침 0" 이
    분할의 성질이 아니라 회계 쪽 규칙의 성질이 되어 아무것도 증명하지 못한다.

    값이 아니라 위치를 내는 이유는 겹침 회계 때문이다: 서로 다른 두 공고가 우연히 같은
    값을 가질 수 있으므로 값 동일성으로 세면 겹침이 부풀고, 그러면 이 계측기부터
    거짓말을 한다.
    """
    return [
        index
        for index, row in enumerate(rows)
        if row.denominator_source == stratum and window.contains(row.opened_at)
    ]


def rows_in_window(
    rows: Sequence[AwardRateTrainingRow],
    window: MaturityWindow,
    *,
    stratum: str = GATE_STRATUM,
) -> list[AwardRateTrainingRow]:
    """창 안의 평가 층 행 — :func:`indices_in_window` 의 값 뷰."""
    return [rows[index] for index in indices_in_window(rows, window, stratum=stratum)]


def _window_facts(
    rows: Sequence[AwardRateTrainingRow], window: MaturityWindow, *, stratum: str
) -> _WindowFacts:
    """규칙 표가 보는 관측 한 벌 — 창 안의 평가 행 수와 그 앞의 학습 행 수."""
    return _WindowFacts(
        window=window,
        evaluation_row_count=len(indices_in_window(rows, window, stratum=stratum)),
        train_row_count=sum(
            1
            for row in rows
            if row.denominator_source == stratum and row.opened_at < window.start
        ),
    )


def _partition_windows(
    rows: Sequence[AwardRateTrainingRow],
    maturity_windows: Sequence[MaturityWindow],
    *,
    policy: WindowPolicy,
    stratum: str,
) -> tuple[list[_WindowFacts], list[WindowExclusion]]:
    """규칙 표를 순서대로 적용해 (통과한 창, 사유와 함께 뺀 창)으로 가른다."""
    survivors: list[_WindowFacts] = []
    excluded: list[WindowExclusion] = []
    for window in sorted(maturity_windows, key=lambda item: item.start):
        facts = _window_facts(rows, window, stratum=stratum)
        reason = next(
            (name for name, rule in _EXCLUSION_RULES if rule(facts, policy)), None
        )
        if reason is None:
            survivors.append(facts)
        else:
            excluded.append(
                WindowExclusion(
                    window=window,
                    reason=reason,
                    evaluation_row_count=facts.evaluation_row_count,
                )
            )
    return survivors, excluded


def plan_evaluation_windows(
    rows: Sequence[AwardRateTrainingRow],
    maturity_windows: Sequence[MaturityWindow],
    *,
    policy: WindowPolicy | None = None,
    stratum: str = GATE_STRATUM,
) -> EvaluationPlan:
    """성숙도 구간 표에서 평가 창을 고른다(embargo + 표본 하한 + 최근 N).

    Args:
        rows: 코퍼스 전체(층 무관). 평가 층 행 수와 학습측 유무를 여기서 센다.
        maturity_windows: :func:`~app.domain.settlement_maturity.build_weekly_maturity`
            의 산출. 겹치지 않는 구간이라 평가 창도 겹치지 않는다.
        policy: 선택 정책(기본값이 선언된 게이트).
        stratum: 평가 층.

    Returns:
        :class:`EvaluationPlan` — 고른 창(시작 시각 오름차순)과 뺀 창(사유 포함).
    """
    active = policy or WindowPolicy()
    survivors, excluded = _partition_windows(
        rows, maturity_windows, policy=active, stratum=stratum
    )
    keep = survivors[-active.max_origins :] if active.max_origins > 0 else []
    dropped = survivors[: len(survivors) - len(keep)]
    excluded.extend(
        WindowExclusion(
            window=facts.window,
            reason=EXCLUDED_BEYOND_MAX_ORIGINS,
            evaluation_row_count=facts.evaluation_row_count,
        )
        for facts in dropped
    )
    return EvaluationPlan(
        selected=tuple(facts.window for facts in keep),
        excluded=tuple(sorted(excluded, key=lambda item: item.window.start)),
        policy=active,
    )


class WindowSummary(StrictModel):
    """평가 창 하나의 경계·성숙도·행 수 — 리포트 행. 제외된 창도 같은 모양으로 남는다."""

    start: str
    end: str
    maturity: float
    opened_count: int
    """성숙도 분모 — 그 창에 개찰된 피드 공고 수."""
    settled_count: int
    """성숙도 분자 — 그중 낙찰가를 아는 수."""
    evaluation_row_count: int
    """그 창에 들어간 평가 층 학습 행 수."""
    excluded_reason: str | None = None
    """제외 사유(위 어휘). 평가에 쓴 창은 ``None``."""


class HoldoutOverlapSummary(StrictModel):
    """두 평가 창의 홀드아웃이 공유하는 행 수 — 리포트 행. **설계상 0** 이어야 한다."""

    first_start: str
    second_start: str
    row_count: int


def summarize_window(
    window: MaturityWindow, *, evaluation_row_count: int, excluded_reason: str | None
) -> WindowSummary:
    """창 하나를 리포트 행으로 편다."""
    return WindowSummary(
        start=window.start.isoformat(),
        end=window.end.isoformat(),
        maturity=window.maturity,
        opened_count=window.opened_count,
        settled_count=window.settled_count,
        evaluation_row_count=evaluation_row_count,
        excluded_reason=excluded_reason,
    )


def summarize_exclusions(plan: EvaluationPlan) -> list[WindowSummary]:
    """embargo·표본 하한·최근 N 으로 뺀 창과 사유 — 제외는 침묵이 아니라 기록이다."""
    return [
        summarize_window(
            item.window,
            evaluation_row_count=item.evaluation_row_count,
            excluded_reason=item.reason,
        )
        for item in plan.excluded
    ]


def summarize_overlaps(
    rows: Sequence[AwardRateTrainingRow],
    windows: Sequence[MaturityWindow],
    *,
    stratum: str = GATE_STRATUM,
) -> list[HoldoutOverlapSummary]:
    """창 쌍의 홀드아웃 겹침을 리포트 행으로 — 겹치지 않는다는 **주장이 아니라 측정**이다."""
    return [
        HoldoutOverlapSummary(
            first_start=overlap.first_start.isoformat(),
            second_start=overlap.second_start.isoformat(),
            row_count=overlap.row_count,
        )
        for overlap in holdout_overlaps(rows, windows, stratum=stratum)
    ]


def holdout_overlaps(
    rows: Sequence[AwardRateTrainingRow],
    windows: Sequence[MaturityWindow],
    *,
    stratum: str = GATE_STRATUM,
) -> list[HoldoutOverlap]:
    """창 쌍마다 홀드아웃이 공유하는 행 수 — 겹침이 **측정된 사실**이 되게 한다."""
    selections = [
        set(indices_in_window(rows, window, stratum=stratum)) for window in windows
    ]
    return [
        HoldoutOverlap(
            first_start=windows[first].start,
            second_start=windows[second].start,
            row_count=len(selections[first] & selections[second]),
        )
        for first in range(len(windows))
        for second in range(first + 1, len(windows))
    ]
