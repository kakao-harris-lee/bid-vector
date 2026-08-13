"""정산 성숙도 — "그 구간에 개찰된 공고 중 결과를 아는 비율" (순수 커널).

왜 이 축이 필요한가
-------------------
홀드아웃은 **정산된 행**만 볼 수 있다. 어떤 구간의 절반만 정산돼 있으면 그 구간에서 잰
수치는 "그 구간의 공고"가 아니라 "그 구간에서 **먼저 정산된** 공고"에서 나온 것이고, 그
선택이 낙찰률과 무관하다는 보장은 없다.

그 선택의 방향은 **현재 스키마로 측정할 수 없다**. "결과를 언제 알았는가"에 해당하는
타임스탬프가 없기 때문이다(실측: ``TenderResult.announced_at`` 은 개찰일시 복사본이라
``announced_at − opened_at`` 의 p50/p90 이 모든 주에서 0.00d, ``created_at`` 은 수집
시각이라 p50 이 −6d, ``opening_checked_at`` 은 11,023행 중 2행만 채워져 있다). 측정할 수
없는 편향은 "없다"고 주장할 수 없으므로, 정직한 대응은 측정이 아니라 **회피**다 — 성숙도가
낮은 구간을 평가에서 뺀다(embargo).

성숙도는 시간의 함수가 **아니다**
---------------------------------
"오래된 구간일수록 성숙하다"는 직관은 이 코퍼스에서 성립하지 않는다(실측 2026-08-13:
2026-W24 0.449 · W25 0.541 · W26 0.515 인데 W28 은 0.822). 개찰 이후 경과 시간뿐 아니라
**수집 커버리지**가 함께 들어가기 때문이다. 그래서 임계는 "몇 주 전인가"가 아니라 관측된
비율로 걸어야 하고, 이 모듈은 그 비율을 데이터에서 도출한다.

구간은 KST 주(월요일 00:00 KST 시작)다. 개찰은 한국 업무시간에 일어나므로 UTC 자정
경계를 쓰면 월요일 오전 개찰이 전주로 밀린다.

I/O 는 없다. 관측을 만드는 얇은 경계는
:mod:`app.services.settlement_maturity` 이고, **미정산 공고에 ``winning_amount`` 가
NULL 이 아니라 0.0 으로 들어 있다**는 함정도 거기 적혀 있다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from app.core.time import ensure_utc, to_kst

__all__ = [
    "MATURITY_WINDOW_LENGTH",
    "MaturityWindow",
    "SettlementObservation",
    "build_weekly_maturity",
    "week_start_utc",
]

# 성숙도 구간의 길이. 주 단위인 이유: 개찰은 평일에 몰려 있어 일 단위 구간은 요일 효과가
# 성숙도를 흔들고, 월 단위는 임계를 넘는 구간이 통째로 사라진다.
MATURITY_WINDOW_LENGTH: Final[timedelta] = timedelta(days=7)


@dataclass(frozen=True)
class SettlementObservation:
    """공고 하나의 (개찰 시각, 결과를 아는가).

    ``settled`` 는 "낙찰가를 아는가"이지 "낙찰이 됐는가"가 아니다 — 유찰·취소도 결과를
    모르는 것과 같은 취급을 받는다. 성숙도가 재는 것은 낙찰 여부가 아니라 **관측
    가능성**이기 때문이다.
    """

    opened_at: datetime
    settled: bool


@dataclass(frozen=True)
class MaturityWindow:
    """구간 ``[start, end)`` 하나의 성숙도.

    ``opened_count`` 는 그 구간에 개찰된 공고 **전체**이고 ``settled_count`` 는 그중 결과를
    아는 것이다. 두 수를 비율과 함께 들고 다니는 이유는, 비율 하나만 남기면 "0.75 인데 4건"
    과 "0.75 인데 4,000건"이 구별되지 않기 때문이다.
    """

    start: datetime
    end: datetime
    opened_count: int
    settled_count: int

    @property
    def maturity(self) -> float:
        """정산 비율. 분모가 0이면 0.0 — 빈 구간은 성숙하지 않은 것으로 본다."""
        if self.opened_count <= 0:
            return 0.0
        return self.settled_count / self.opened_count

    def contains(self, value: datetime) -> bool:
        """``value`` 가 ``[start, end)`` 안인가 — 경계는 **시작 포함, 끝 제외**다.

        이 반개구간 규칙이 두 성질을 동시에 준다: 인접 구간이 겹치지 않고, 학습 범위
        (``opened_at < start``)와 평가 범위가 경계 시각에서 서로 배타적이다.
        """
        return self.start <= ensure_utc(value) < self.end


def week_start_utc(value: datetime) -> datetime:
    """``value`` 가 속한 **KST 주**의 시작(월요일 00:00 KST)을 UTC 로 돌려준다."""
    local = to_kst(value)
    monday = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=local.weekday()
    )
    return ensure_utc(monday)


def build_weekly_maturity(
    observations: Sequence[SettlementObservation],
) -> tuple[MaturityWindow, ...]:
    """관측을 KST 주로 묶어 성숙도 구간 표를 만든다(시작 시각 오름차순).

    관측이 없는 주는 표에 나타나지 않는다. 빈 주를 0/0 으로 채우면 "그 주에는 개찰이
    없었다"와 "그 주는 하나도 정산되지 않았다"가 같은 행으로 보인다.

    Args:
        observations: 공고별 (개찰 시각, 결과 관측 여부). 순서는 상관없다.

    Returns:
        시작 시각 오름차순의 :class:`MaturityWindow` 목록.
    """
    totals: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
    for observation in observations:
        counts = totals[week_start_utc(observation.opened_at)]
        counts[0] += 1
        counts[1] += 1 if observation.settled else 0
    return tuple(
        MaturityWindow(
            start=start,
            end=start + MATURITY_WINDOW_LENGTH,
            opened_count=opened,
            settled_count=settled,
        )
        for start, (opened, settled) in sorted(totals.items())
    )
