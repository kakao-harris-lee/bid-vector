"""국가계약 공사 적격심사 낙찰하한율 — 시행일·추정가격 구간 인지 선언 테이블.

(National-contract construction qualification-examination minimum-award-rate
floors, declared as an effective-date × 추정가격-tier lookup table.)

배경
----
공사 적격심사 낙찰하한율은 flat 값이 아니라 **추정가격 구간별**로 다르고, 2026-01-30
개정으로 전 구간 **+2%p** 상향됐다. 기존 guardrail 은 공사에 flat ``0.87`` 하나만
써서 개정 후 하한(최소 87.495%)에 전 구간 미달했다. 이 모듈은 그 표를 코드 분기가
아니라 **선언 데이터**로 모으고(§4.5.3), 순수 리졸버가 이를 **해석만** 한다(§4.7.4).

시행일 인지가 필수인 이유(시간 누수 차단)
-----------------------------------------
백테스트가 과거 공고에 **신율을 소급 적용**하면 그 시점에 존재하지 않던 하한으로 과거
guardrail 이 왜곡된다(leakage). 따라서 리졸버는 반드시 공고 기준일을 받아 그 시점에
유효했던 구/신율을 고른다. 기준일을 모르면(``None``) 표를 적용하지 않고 ``None`` 을
돌려준다 — 호출부는 기존 flat 하한을 그대로 유지한다(최소 놀람·소급 방지).

경계·범위(caveat)
-----------------
- 값은 **예정가격 기준** 율이다. 우리 투찰가는 기초금액(사업금액) 기준(#162)이므로,
  guardrail 통합부(``guardrail_core.resolve_floor_bid_rate``)가 E[사정률]을 곱해
  기초금액 기준으로 변환한 뒤 기존 category/group/legal 하한과 ``max()`` 로 결합한다.
  이 모듈 자체는 예정가-기준 값만 반환하며 변환은 하지 않는다.
- 구간 판정 입력은 공고의 **추정가격**(``Project.budget_estimate``)이다. 추정가격은
  과세 여부에 따라 VAT 포함/제외가 비일관적일 수 있어(메모리 참조) 10억/50억/100억
  **경계 부근에서 구간 오분류** 가능성이 있다. 기초금액으로 추정가격을 역산하지
  않는다(스코프 밖) — 추정가격이 없으면 표 미적용(``None``).
- 여기 모델링하는 것은 **국가계약** 표뿐이다. 지방계약(지자체)은 별도 율 체계로
  2025-07-01 기시행됐고 이번 스코프에서 제외한다.
- 추정가격 **100억 이상**은 종합심사낙찰제(종심제) 대상으로 하한율 표의 대상이
  아니다 → 해당 구간은 브래킷을 두지 않아 ``None`` 을 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# 억 단위 상수(KRW). 구간 경계는 법령 표기 그대로 [하한, 상한).
_EOK = 100_000_000.0
_10_EOK = 10 * _EOK  # 1,000,000,000
_50_EOK = 50 * _EOK  # 5,000,000,000
_100_EOK = 100 * _EOK  # 10,000,000,000 — 이상은 종심제(표 대상 아님)

# 개정 시행일(+2%p). 이 날짜(포함) 이후 신율.
CONSTRUCTION_FLOOR_REVISION_DATE = date(2026, 1, 30)


@dataclass(frozen=True)
class _FloorBracket:
    """추정가격 ``[lower, upper)`` 구간의 예정가격-기준 낙찰하한율."""

    lower: float  # inclusive
    upper: float | None  # exclusive; None == 무한대
    floor_rate: float  # 예정가격 기준 율(0-1)

    def matches(self, amount: float) -> bool:
        if amount < self.lower:
            return False
        return self.upper is None or amount < self.upper


@dataclass(frozen=True)
class _FloorSchedule:
    """한 시행일부터 유효한 구간별 하한율 표."""

    effective_from: date  # inclusive
    brackets: tuple[_FloorBracket, ...]

    def resolve(self, amount: float) -> float | None:
        for bracket in self.brackets:
            if bracket.matches(amount):
                return bracket.floor_rate
        return None  # 어떤 브래킷에도 없음(예: 100억+ 종심제) → 표 대상 아님


# 선언 테이블 — effective_from 내림차순(최신 우선)으로 배열해 리졸버가 기준일 이하
# 가장 최근 시행일을 첫 매칭으로 고른다. 100억 이상 구간은 종심제라 어느 표에도
# 브래킷을 두지 않는다(=> None).
_NATIONAL_CONSTRUCTION_FLOOR_SCHEDULES: tuple[_FloorSchedule, ...] = (
    # 신율(2026-01-30 개정, +2%p).
    _FloorSchedule(
        effective_from=CONSTRUCTION_FLOOR_REVISION_DATE,
        brackets=(
            _FloorBracket(0.0, _10_EOK, 0.89745),  # 10억 미만
            _FloorBracket(_10_EOK, _50_EOK, 0.88745),  # 10억 이상 ~ 50억 미만
            _FloorBracket(_50_EOK, _100_EOK, 0.87495),  # 50억 이상 ~ 100억 미만
        ),
    ),
    # 구율(개정 전).
    _FloorSchedule(
        effective_from=date.min,
        brackets=(
            _FloorBracket(0.0, _10_EOK, 0.87745),  # 10억 미만
            _FloorBracket(_10_EOK, _50_EOK, 0.86745),  # 10억 이상 ~ 50억 미만
            _FloorBracket(_50_EOK, _100_EOK, 0.85495),  # 50억 이상 ~ 100억 미만
        ),
    ),
)


def _coerce_amount(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coerce_reference_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    # datetime is a subclass of date — narrow to the calendar day first.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _select_schedule(reference_date: date) -> _FloorSchedule:
    for schedule in _NATIONAL_CONSTRUCTION_FLOOR_SCHEDULES:
        if reference_date >= schedule.effective_from:
            return schedule
    # date.min sentinel makes this unreachable; kept explicit for total-ness.
    return _NATIONAL_CONSTRUCTION_FLOOR_SCHEDULES[-1]


def resolve_construction_qualification_floor(
    estimation_amount: float | None,
    reference_date: date | datetime | None,
) -> float | None:
    """국가계약 공사 적격심사 낙찰하한율(예정가격 기준)을 해석한다.

    Args:
        estimation_amount: 공고 추정가격(KRW). 구간 판정 입력.
        reference_date: 공고 기준일(입찰공고/개찰 등). 시행일(구/신율) 판단용.

    Returns:
        예정가격 기준 낙찰하한율(0-1). 다음이면 ``None``:
          - 추정가격 불명/0/음수
          - 기준일 불명(시행일 판단 불가 → 소급 적용 방지)
          - 추정가격 100억 이상(종심제 — 하한율 표 대상 아님)

    이 값은 예정가-기준이다. 기초금액 기준 변환(E[사정률])과 기존 floor 결합은
    호출부(guardrail_core)가 담당한다 — 이 리졸버는 표 해석만 하는 순수 함수다.
    """
    amount = _coerce_amount(estimation_amount)
    if amount is None or amount <= 0:
        return None
    ref = _coerce_reference_date(reference_date)
    if ref is None:
        return None
    return _select_schedule(ref).resolve(amount)
