"""셀 분할 — ``공종 × era tier × 금액대`` 배정과 그 위의 부분군 선택 (순수).

왜 이 축인가
------------
운영자 결정(설계 §12-4)이 셀 스코프를 ``공종 × era tier × 금액대``로 못박고, 발주기관은
셀 키가 아니라 **수축 상위 계층**으로만 쓴다(Phase 1 실측 91.9% 가 n<10 인 표본 구조).
era tier 가 셀 키에 들어간 이유는 식 3 의 f-재기준이 f-이질 행을 가로질러 pool 하지
않게 하려는 것인데(NEW-4), **그 제약이 실제로 작동하는지는 가정이 아니라 측정 대상**
이다 — :func:`floor_rate_distribution` 이 셀 안의 f 분포를 그대로 내어 F1(era tier 가
f 를 동질화하는가) 판정 재료를 만든다.

세 종류의 부분군
----------------
* **셀 전체** — 층 A 접지 진리의 모집단.
* **f-동질 부분군**(:func:`dominant_floor_subgroup`) — 층 A 와 층 C 를 **같은 f** 위에서
  비교하기 위한 스코프. 층 C 는 단일 ``floor_rate`` 를 받으므로 f 가 섞인 모집단에
  그대로 대면 두 곡선의 차이에 "합성 가정의 효과"와 "f 이질성"이 뒤섞인다.
* **as-of 분할**(:func:`split_as_of`) — 과거로 적합하고 미래를 채점하는 시간 분할.
  ``G`` 와 ``F_a`` 는 과거 정산 공고만 봐야 한다(설계 §8.2 시간 누수 #3).

순수 함수(I/O 0) — 입력은 :class:`~app.services.ml_training.award_landing_ladder.LandingRow`
값 목록뿐이고 DB·시계·난수를 만지지 않는다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.services.ml_training.award_landing_ladder import LandingRow

__all__ = [
    "AwardLandingCell",
    "FloorRateShare",
    "as_of_split_boundary",
    "dominant_floor_subgroup",
    "floor_rate_distribution",
    "group_by_category",
    "group_by_cell",
    "split_as_of",
]

# as-of 분할의 적합 구간 비율. 시간 분할 OOS(G5)는 "older 적합 / newer 채점"이고, 이
# 비율은 **판정 임계가 아니라 분할 위치**다 — 여기서 고르는 것은 통과 기준이 아니라
# 얼마나 많은 과거를 쓸지이므로 사후 산물 위험이 없다. 0.7 은 얕은 셀에서 채점 구간이
# 한 자릿수로 내려가지 않게 하면서 적합 구간에 다수를 남기는 관례값이다.
DEFAULT_AS_OF_FIT_SHARE: Final[float] = 0.7


@dataclass(frozen=True)
class AwardLandingCell:
    """셀 키 — 공종 × era tier × 금액대(§4.5-2 선언 키)."""

    category: str
    era_tier: str
    amount_band: str

    @property
    def key(self) -> str:
        """리포트·로그가 공유하는 사람이 읽는 키."""
        return f"{self.category}|{self.era_tier}|{self.amount_band}"


@dataclass(frozen=True)
class FloorRateShare:
    """셀 안의 게시 하한율 하나와 그 점유 — F1 판정 재료."""

    floor_rate: float
    row_count: int
    share: float


def cell_of(row: LandingRow) -> AwardLandingCell:
    return AwardLandingCell(
        category=row.category, era_tier=row.era_tier, amount_band=row.amount_band
    )


def group_by_cell(
    rows: Sequence[LandingRow],
) -> dict[AwardLandingCell, tuple[LandingRow, ...]]:
    """행을 셀별로 모은다(각 셀 안은 개찰 시각 오름차순 유지)."""
    grouped: dict[AwardLandingCell, list[LandingRow]] = defaultdict(list)
    for row in rows:
        grouped[cell_of(row)].append(row)
    return {
        cell: tuple(sorted(items, key=lambda item: (item.opened_at, item.project_id)))
        for cell, items in grouped.items()
    }


def group_by_category(
    rows: Sequence[LandingRow],
) -> dict[str, tuple[LandingRow, ...]]:
    """수축 상위 계층(공종) — 얕은 셀의 마진 분포가 접혀 들어갈 부모."""
    grouped: dict[str, list[LandingRow]] = defaultdict(list)
    for row in rows:
        grouped[row.category].append(row)
    return {
        category: tuple(
            sorted(items, key=lambda item: (item.opened_at, item.project_id))
        )
        for category, items in grouped.items()
    }


def floor_rate_distribution(rows: Sequence[LandingRow]) -> tuple[FloorRateShare, ...]:
    """셀 안의 게시 하한율 분포(점유 내림차순).

    설계는 service 셀에서 ``f=0.88`` 66.8% · ``0.90`` 14.3% · ``0.87745`` 7.2% 를
    인용한다. 그 수치의 재산출본이자, era tier 가 f 를 동질화하지 **못하면** f-밴드를
    셀 키로 올려야 한다는 재론(F1)의 근거다.
    """
    if not rows:
        return ()
    counts = Counter(row.floor_rate for row in rows)
    total = float(len(rows))
    return tuple(
        FloorRateShare(floor_rate=rate, row_count=count, share=count / total)
        for rate, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def dominant_floor_subgroup(
    rows: Sequence[LandingRow],
) -> tuple[float | None, tuple[LandingRow, ...]]:
    """가장 많은 ``f`` 를 공유하는 부분군 — 층 A vs 층 C 를 같은 축에서 비교하는 스코프.

    최빈 f 를 고르는 것은 **최적화가 아니라 축 정합**이다: 층 C 는 단일 ``floor_rate``
    를 받으므로 f 가 섞인 모집단과는 애초에 같은 대상을 재지 않는다. 동률이면 낮은 f
    를 고른다(결정성 — 실행마다 비교 스코프가 흔들리면 수치가 재현되지 않는다).
    """
    distribution = floor_rate_distribution(rows)
    if not distribution:
        return None, ()
    dominant = distribution[0].floor_rate
    return dominant, tuple(row for row in rows if row.floor_rate == dominant)


def as_of_split_boundary(
    rows: Sequence[LandingRow], *, fit_share: float
) -> datetime | None:
    """적합/채점 경계 **시각**. 나눌 distinct 시각이 2개 미만이면 ``None``.

    **행 인덱스로 자르면 안 된다**(B1 회귀): 이 코퍼스는 개찰 시각이 심하게 뭉쳐 있어
    (실측 1,791행에 distinct 시각 107개, 최대 동률 232행) 인덱스 경계가 동률 그룹
    **내부**에 떨어진다. 그러면 같은 시각의 형제 행이 적합과 채점으로 갈라지고, 채점
    행과 완전히 동시에 관측된 정보로 적합한 분포가 그 행을 채점하게 된다 — G2 의
    "겹치지 않는 창"을 정의부터 위반하고, 방향은 층 C 에 유리하다(누수는 항상 낙관).

    그래서 경계 후보는 **distinct 시각**뿐이고, 동률 그룹은 통째로 한쪽에 간다. 목표
    비율 ``fit_share`` 에 가장 가까운 경계를 고르되 동률이면 **이른** 경계를 택한다
    (결정성 — 실행마다 창이 흔들리면 수치가 재현되지 않는다).

    이 수정의 성과는 **누수의 구조적 제거**이지 "수치가 얼마 움직였다"가 아니다(N3).
    수정 전후 as-of 수치가 움직인 것은 경계가 옮겨지며 적합/채점 **모집단 자체**가
    바뀐 결과가 지배하고 이동 방향도 셀마다 갈린다 — 누수 크기와 이동 크기는 실측에서
    오히려 반대로 움직였다(:func:`~app.services.ml_training.award_landing_curves.
    parent_margin_distribution` 의 실측 인용).
    """
    if not 0.0 < fit_share < 1.0:
        raise ValueError("fit_share must be strictly between 0 and 1")
    counts = Counter(row.opened_at for row in rows)
    stamps = sorted(counts)
    if len(stamps) < 2:
        return None
    target = len(rows) * fit_share
    cumulative = 0
    best_stamp = stamps[1]
    best_gap: float | None = None
    for position, stamp in enumerate(stamps[:-1]):
        cumulative += counts[stamp]
        gap = abs(cumulative - target)
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_stamp = stamps[position + 1]
    return best_stamp


def split_as_of(
    rows: Sequence[LandingRow], *, fit_share: float = DEFAULT_AS_OF_FIT_SHARE
) -> tuple[tuple[LandingRow, ...], tuple[LandingRow, ...]]:
    """개찰 시각 기준 (적합, 채점) 분할 — random split 금지(G5), 동률 시각 분리 금지(B1).

    경계 시각 **미만**이 적합, **이상**이 채점이다. 그래서 두 창의 개찰 시각 집합은
    교집합이 공집합이고, 채점 행과 같은 시각의 행은 적합에 절대 들어가지 않는다.

    분할이 성립하지 않으면(=distinct 시각이 하나뿐) ``((), ())`` 를 돌려준다 — 한쪽이
    비어 있거나 시각이 겹치는 "분할"을 조용히 돌려주면 그것이 곧 누수다.
    """
    ordered = tuple(rows)
    boundary = as_of_split_boundary(ordered, fit_share=fit_share)
    if boundary is None:
        return (), ()
    fit = tuple(row for row in ordered if row.opened_at < boundary)
    score = tuple(row for row in ordered if row.opened_at >= boundary)
    return (fit, score) if fit and score else ((), ())
