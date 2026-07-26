"""홀드아웃 대상 행의 분모·법정하한 데이터 품질 플래그 — 선언 규칙 테이블.

배경(로드맵 5·12번)
--------------------
가격 오차 지표는 **분모가 맞을 때만** 의미가 있다. 이 저장소에서 반복된 회귀의
근본원인이 정확히 그것이었다: ``base_amount`` 가 예정가 역산/VAT 파생으로 오염되고
([[recurring-bug-root-cause-basis]] · #199), ``winning_rate`` 는 소스마다 예정가 기준과
기초금액 기준이 섞이며, 공사 법정 낙찰하한은 시행일별로 달라졌다(#197/#198).

이 모듈은 홀드아웃 한 행에 대해 그 세 축을 **읽기 전용으로 판정**만 한다:

1. 보고된 낙찰률(``winning_rate``)과 ``winning_amount / base_amount`` 의 **상대** 불일치
2. 낙찰률이 era-correct 법정 낙찰하한(공고 자신의 published 하한 우선)을 하회
3. ``base_amount`` basis 오염 태그(clean 여부)

2번은 **하한 모델이 그 공고에 적용되는 경우에만** 수행한다. 적용 범위 판별(발주기관
유형 + 게시 하한율 개연성)은 :mod:`app.ai.floor_applicability` 선언 테이블에 위임한다.

판정 규칙은 코드 분기가 아니라 ``_QUALITY_RULES`` 선언 테이블이고 해석기는 그것을
순회만 한다(§4.5.2/§4.5.3). 허용오차·임계값은 전부 모듈 상수로 선언한다(매직값 금지).

경계(중요 — 오탐 방지)
----------------------
법정 하한율은 **예정가격 기준** 값이다(``legal_floor_spec`` 참조). 우리 오차 측정용
``amount_derived_rate`` 는 **기초금액 기준**(#162)이라 사정률(<1)만큼 낮게 나오므로,
하한 하회 판정에 그 값을 쓰면 정상 낙찰이 대량 오탐된다. 따라서 하한 비교는 소스가
보고한 ``winning_rate`` 가 있을 때만 수행하고, 없으면 **판정을 생략**한다(플래그 없음).

보고값이 **있어도** 그것이 독립적인 예정가-basis 실측이 아니라 우리가 가진 금액비의
파생값이면 같은 오탐이 그대로 재현된다(라이브 실측 13건). 그래서 보고율이 금액-역산율과
사실상 일치하고 독립 예정가 증거(복수예비가격)도 없으면 하한 판정을 생략하고 그 사실을
``rate_basis_unverified`` 로 남긴다(:func:`_is_rate_basis_unverified`).

이 모듈은 예측·guardrail 을 전혀 건드리지 않는 분석 전용 판정기다.

순수 함수(I/O 0).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.ai.construction_scenario import is_construction_era_floor_resolved
from app.ai.floor_applicability import (
    FLOOR_APPLICABLE,
    FLOOR_SEPARATE_REGIME,
    FORESTRY_REGIME_FLOOR_RATE,
    is_floor_judgeable,
    is_published_floor_plausible,
    resolve_floor_applicability,
)
from app.ai.predictors.legal_floor_spec import (
    resolve_construction_qualification_floor,
)
from app.domain.aggregates import error_rate
from app.services.base_amount_basis import BASIS_CLEAN

# ── 플래그 라벨 ────────────────────────────────────────────────────────────────
FLAG_LOW_ACTUAL_RATE = "low_actual_rate"
FLAG_CONSTRUCTION_LOW_RATE_REVIEW = "construction_low_rate_review"
FLAG_MISSING_AMOUNT_DERIVED_RATE = "missing_amount_derived_rate"
FLAG_MISSING_REPORTED_RATE = "missing_reported_rate"
FLAG_AMOUNT_RATE_MISMATCH = "amount_rate_mismatch"
FLAG_BELOW_LEGAL_FLOOR = "below_legal_floor"
FLAG_BASE_BASIS_CONTAMINATED = "base_basis_contaminated"

# 플래그가 하나도 없는 행이 clean/flag 분리 집계에서 쓰는 라벨.
CLEAN_FLAG_LABEL = "clean"

# ── 허용오차·임계값(선언) ─────────────────────────────────────────────────────
# 보고 낙찰률 vs 금액-역산 낙찰률의 **상대** 허용오차. 두 값이 같은 분모를 쓴다면
# 반올림 잡음(원 단위 절사 등)만 남으므로 1%면 충분히 느슨하다. 절대차 기준을 쓰면
# 저율 구간(예: 0.6)과 고율 구간(0.99)에 같은 잣대를 대게 되어 저율 쪽이 둔감해진다.
RATE_MISMATCH_RELATIVE_TOLERANCE = 0.01
# 법정 하한 하회 판정의 절대 허용오차(=5bp). 하한율 표기 반올림과 원 단위 절사로
# 생기는 미세 하회를 오탐하지 않기 위한 여유다.
LEGAL_FLOOR_UNDERCUT_TOLERANCE = 0.0005
# 보고 낙찰률이 **독립 실측인지 금액비 파생인지**를 가르는 상대오차 경계.
# 라이브 실측(2026-07-26 기관 축 홀드아웃)에서 두 군집이 이 값을 사이에 두고 깨끗이
# 갈렸다: 파생 의심군 13건의 상대오차는 ≤1e-5(6건은 정확히 0.0), 독립 예정가-basis 로
# 확인된 행들의 **최소** 상대오차는 1.4e-3. 그 사이 값은 관측되지 않았다.
RATE_BASIS_INDEPENDENCE_TOLERANCE = 1e-3
# 독립 예정가 증거로 인정하는 복수예비가격 최소 개수. KONEPS 복수예비가격은 15개가
# 정상이지만 부분 적재 행도 예정가 재구성이 가능하므로 보수적으로 5개를 경계로 둔다.
# 이 증거가 있으면 보고율이 금액비와 일치해도(사정률≈1 인 정상 케이스) 판정을 유지한다.
MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE = 5
# 가격 백테스트 대상으로 쓰기 어려운 저율 구간(기존 홀드아웃 판정을 그대로 승계).
LOW_ACTUAL_RATE_THRESHOLD = 0.75
CONSTRUCTION_LOW_RATE_THRESHOLD = 0.85
CONSTRUCTION_GROUP = "construction"

# ── 법정 하한 출처 라벨 ───────────────────────────────────────────────────────
FLOOR_SOURCE_PUBLISHED = "published_award_floor_rate"
FLOOR_SOURCE_ERA_TIER = "construction_era_tier"
# 산림사업(separate_regime) — 산림청예규 제728호 체계의 하한(원문 대조 2026-07-26,
# 선언은 app.ai.floor_applicability.FORESTRY_REGIME_FLOOR_RATE).
FLOOR_SOURCE_FORESTRY = "forestry_regime_spec"
FLOOR_SOURCE_NONE = "unresolved"


@dataclass(frozen=True)
class _QualityContext:
    """규칙 테이블이 읽는 정규화된 입력."""

    group: str
    basis: str
    reported_rate: float | None  # 소스가 보고한 낙찰률(예정가 기준일 수 있음)
    effective_rate: float  # 리포트가 실제로 쓰는 낙찰률(보고값 또는 역산값)
    amount_derived_rate: float  # winning_amount / pricing base(기초금액 기준)
    legal_floor_rate: float | None
    rate_mismatch_ratio: float | None
    floor_applicability: str  # applicable / not_applicable / uncertain / separate_regime
    reserve_price_count: int  # 수집된 복수예비가격 개수(독립 예정가 증거의 유무)


def _is_low_actual_rate(ctx: _QualityContext) -> bool:
    return ctx.effective_rate < LOW_ACTUAL_RATE_THRESHOLD


def _is_construction_low_rate(ctx: _QualityContext) -> bool:
    return (
        ctx.group == CONSTRUCTION_GROUP
        and ctx.effective_rate < CONSTRUCTION_LOW_RATE_THRESHOLD
    )


def _is_missing_amount_derived_rate(ctx: _QualityContext) -> bool:
    return ctx.amount_derived_rate <= 0


def _is_missing_reported_rate(ctx: _QualityContext) -> bool:
    """보고 낙찰률이 없으면 분모 정합 검사도 하한 검사도 근거가 없다."""
    return ctx.reported_rate is None


def _is_amount_rate_mismatch(ctx: _QualityContext) -> bool:
    return (
        ctx.rate_mismatch_ratio is not None
        and ctx.rate_mismatch_ratio > RATE_MISMATCH_RELATIVE_TOLERANCE
    )


def _is_floor_comparable(ctx: _QualityContext) -> bool:
    """하한 비교의 **전제**(적용 범위·보고율·해석된 하한)가 모두 충족됐는가.

    하한 모델이 그 공고에 적용되지 않거나(비국가기관), 적용 여부를 이름으로 가릴 수
    없으면(대학교 등) 판정하지 않는다. 산림청 계열(``separate_regime``)은 2026-07-26
    원문 확정 이후 산림사업 하한으로 **판정을 수행**한다(하한 해석은
    :func:`resolve_legal_floor_rate` 가 출처를 갈아끼운다). 해석된 하한값 자체는
    리포트에 남고 ``floor_applicability`` 로 판정 경로/생략 사유가 드러난다.

    ``reported_rate`` 가 없어도 판정하지 않는다 — 기초금액 기준 역산율로 예정가 기준
    하한을 재면 사정률만큼 구조적으로 낮아 정상 낙찰이 오탐된다(모듈 docstring).
    """
    return (
        is_floor_judgeable(ctx.floor_applicability)
        and ctx.reported_rate is not None
        and ctx.legal_floor_rate is not None
    )


def _is_rate_basis_unverified(ctx: _QualityContext) -> bool:
    """보고 낙찰률이 금액비 파생으로 보이고 독립 예정가 증거도 없는가.

    보고율이 ``winning_amount / base_amount`` 와 상대오차 경계 밖으로 갈리면 그 값은
    우리가 가진 금액비로 만들 수 없는 정보(=예정가-basis 실측)를 담고 있다는 뜻이다.
    반대로 사실상 일치하면 소스가 금액비를 되돌려준 것일 수 있고, 그 값을 예정가-basis
    하한에 대면 사정률만큼 구조적으로 낮게 나온다.

    복수예비가격이 충분히 있으면 예정가를 독립적으로 재구성할 수 있으므로, 일치해도
    (사정률≈1 인 정상 케이스) 판정을 유지한다.
    """
    if ctx.reserve_price_count >= MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE:
        return False
    if ctx.rate_mismatch_ratio is None:
        return False
    return ctx.rate_mismatch_ratio < RATE_BASIS_INDEPENDENCE_TOLERANCE


def _is_floor_verdict_rate_basis_blocked(ctx: _QualityContext) -> bool:
    """다른 전제는 다 충족했는데 rate basis 미검증이라 하한 판정을 생략하는가."""
    return _is_floor_comparable(ctx) and _is_rate_basis_unverified(ctx)


def _is_below_legal_floor(ctx: _QualityContext) -> bool:
    """보고 낙찰률이 해석된 법정 하한을 (허용오차 밖으로) 하회하는가."""
    if not _is_floor_comparable(ctx) or _is_rate_basis_unverified(ctx):
        return False
    reported, floor = ctx.reported_rate, ctx.legal_floor_rate
    return (
        reported is not None
        and floor is not None
        and reported < floor - LEGAL_FLOOR_UNDERCUT_TOLERANCE
    )


def _is_base_basis_contaminated(ctx: _QualityContext) -> bool:
    return ctx.basis != BASIS_CLEAN


# 선언 규칙 테이블: 첫 매칭이 아니라 **모든 매칭**을 수집한다(한 행이 여러 품질
# 문제를 동시에 가질 수 있다). 새 검사 = 테이블 한 줄 + 순수 술어 하나.
_QUALITY_RULES: tuple[tuple[str, Callable[[_QualityContext], bool]], ...] = (
    (FLAG_LOW_ACTUAL_RATE, _is_low_actual_rate),
    (FLAG_CONSTRUCTION_LOW_RATE_REVIEW, _is_construction_low_rate),
    (FLAG_MISSING_AMOUNT_DERIVED_RATE, _is_missing_amount_derived_rate),
    (FLAG_MISSING_REPORTED_RATE, _is_missing_reported_rate),
    (FLAG_AMOUNT_RATE_MISMATCH, _is_amount_rate_mismatch),
    (FLAG_BELOW_LEGAL_FLOOR, _is_below_legal_floor),
    (FLAG_BASE_BASIS_CONTAMINATED, _is_base_basis_contaminated),
)

ALL_QUALITY_FLAGS: tuple[str, ...] = tuple(flag for flag, _ in _QUALITY_RULES)


@dataclass(frozen=True)
class LegalFloorResolution:
    """해석된 법정 낙찰하한율 + 출처 + 게시값 신뢰 여부."""

    rate: float | None
    source: str
    published_floor_implausible: bool


def resolve_legal_floor_rate(
    *,
    published_floor_rate: float | None,
    category: str | None,
    estimation_amount: float | None,
    reference_date: date | datetime | None,
    floor_applicability: str = FLOOR_APPLICABLE,
) -> LegalFloorResolution:
    """공고에 적용되는 법정 낙찰하한율과 그 출처를 해석한다(선언 우선순위).

    1. 공고 자신이 게시한 낙찰하한율(``Project.award_floor_rate``, #201) — 공고 시점
       공개값이라 leakage-safe 하고 가장 구체적이다. 단 **개연 범위 안일 때만** 쓴다
       (:mod:`app.ai.floor_applicability` 선언 상수). 라이브에 ``1.00000`` 으로
       적재된 값이 있어 그대로 쓰면 정상 낙찰이 하회로 오탐된다.
    2. ``separate_regime``(산림청 계열)이면 산림사업 하한
       :data:`FORESTRY_REGIME_FLOOR_RATE`(예규 728호, 원문 대조 2026-07-26) — 국가계약
       era-tier 는 이 공고들에 범주 오류라 적용하지 않는다.
    3. era-correct 공사 적격심사 tier(#197 선언 테이블). 공고 자신의 기준일로
       해석하므로 과거 공고에 신율이 소급되지 않는다.

    해석 불가면 ``rate=None`` / ``source="unresolved"`` — 하한 하회 검사는
    생략된다. 모든 값은 **예정가격 기준**이며, 이 함수는 표 해석만 하고 basis 변환은
    하지 않는다.

    이 판정은 **분석 전용**이다. 라이브 예측이 같은 게시값을 guardrail 하한으로
    쓰는 경로(``predict_price(legal_floor_bid_rate=...)``)는 건드리지 않는다.
    """
    implausible = False
    if published_floor_rate is not None and float(published_floor_rate) > 0:
        if is_published_floor_plausible(float(published_floor_rate)):
            return LegalFloorResolution(
                rate=float(published_floor_rate),
                source=FLOOR_SOURCE_PUBLISHED,
                published_floor_implausible=False,
            )
        implausible = True
    if floor_applicability == FLOOR_SEPARATE_REGIME:
        return LegalFloorResolution(
            rate=FORESTRY_REGIME_FLOOR_RATE,
            source=FLOOR_SOURCE_FORESTRY,
            published_floor_implausible=implausible,
        )
    if is_construction_era_floor_resolved(category, estimation_amount, reference_date):
        tier = resolve_construction_qualification_floor(estimation_amount, reference_date)
        if tier is not None:
            return LegalFloorResolution(
                rate=float(tier),
                source=FLOOR_SOURCE_ERA_TIER,
                published_floor_implausible=implausible,
            )
    return LegalFloorResolution(
        rate=None,
        source=FLOOR_SOURCE_NONE,
        published_floor_implausible=implausible,
    )


@dataclass(frozen=True)
class QualityAssessment:
    """한 홀드아웃 행의 품질 판정 결과."""

    flags: tuple[str, ...]
    legal_floor_rate: float | None
    legal_floor_source: str
    rate_mismatch_ratio: float | None
    floor_undercut: float | None  # 하한 − 보고율(하회 시에만 양수), 아니면 None
    floor_applicability: str = FLOOR_APPLICABLE
    published_floor_implausible: bool = False
    # 하한 비교의 다른 전제는 충족했지만 보고율이 금액비 파생으로 보여 생략한 경우.
    rate_basis_unverified: bool = False
    reserve_price_count: int = 0

    def as_details(self) -> dict[str, Any]:
        """리포트 JSON 에 실을 판정 근거(플래그가 왜 붙었는지 추적 가능하게)."""
        return {
            "legal_floor_rate": self.legal_floor_rate,
            "legal_floor_source": self.legal_floor_source,
            "rate_mismatch_ratio": self.rate_mismatch_ratio,
            "rate_mismatch_tolerance": RATE_MISMATCH_RELATIVE_TOLERANCE,
            "floor_undercut": self.floor_undercut,
            "floor_undercut_tolerance": LEGAL_FLOOR_UNDERCUT_TOLERANCE,
            # 하한 하회 판정을 왜 했는지/왜 생략했는지가 행 단위로 추적돼야 한다
            # (침묵 스킵 금지 — 건수는 summary.floor_applicability_counts).
            "floor_applicability": self.floor_applicability,
            "published_floor_implausible": self.published_floor_implausible,
            # 보고율이 독립 실측이 아니라 금액비 파생으로 보여 하한 판정을 생략했는지와
            # 그 판단 근거(예비가 개수·경계). 건수는 summary.rate_basis_unverified_count.
            "rate_basis_unverified": self.rate_basis_unverified,
            "rate_basis_independence_tolerance": RATE_BASIS_INDEPENDENCE_TOLERANCE,
            "reserve_price_count": self.reserve_price_count,
        }


def assess_row_quality(
    *,
    group: str | None,
    category: str | None = None,
    basis: str = BASIS_CLEAN,
    reported_rate: float | None,
    effective_rate: float,
    amount_derived_rate: float,
    published_floor_rate: float | None = None,
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
    agency_name: Any = None,
    reserve_price_count: int = 0,
) -> QualityAssessment:
    """홀드아웃 한 행의 품질 플래그를 판정한다(순수).

    Args:
        group: 업무구분(공사/용역/물품). 공사 전용 저율 검토 플래그에 쓰인다.
        category: 정규화 카테고리. era-correct 공사 tier 해석 게이트.
        basis: ``classify_base_basis`` 판정값(#199). clean 이 아니면 오염 플래그.
        reported_rate: 소스가 보고한 낙찰률(분수). 없으면 ``None``.
        effective_rate: 리포트가 실제 오차 계산에 쓴 낙찰률.
        amount_derived_rate: ``winning_amount / pricing base``(기초금액 기준).
        published_floor_rate: 공고 게시 낙찰하한율(분수) — 개연 범위 안이면 최우선.
        estimation_amount: 추정가격. 공사 tier 구간 판정 입력.
        reference_date: 공고 기준일. 구/신율(시행일) 판정 입력 — 소급 방지.
        agency_name: 발주기관 표시명(부재 시 수요기관). 하한 모델 적용 범위 판별
            입력이며, 생략하면 ``applicable`` 로 보아 기존 판정을 유지한다.
        reserve_price_count: 그 공고에 수집된 복수예비가격 개수(호출부가 주입 — §4.7.3).
            독립 예정가 증거의 유무로만 쓰이며, 생략하면 0(증거 없음)으로 본다.
    """
    floor_applicability = resolve_floor_applicability(agency_name)
    floor = resolve_legal_floor_rate(
        published_floor_rate=published_floor_rate,
        category=category,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
        floor_applicability=floor_applicability,
    )
    legal_floor_rate, legal_floor_source = floor.rate, floor.source
    # 상대 불일치는 집계 단일 출처(app.domain.aggregates.error_rate)에 위임한다 —
    # 분모는 금액-역산율(측정 가능한 값), 분자는 보고율과의 차이.
    rate_mismatch_ratio = (
        error_rate(reported_rate, amount_derived_rate)
        if amount_derived_rate > 0
        else None
    )
    ctx = _QualityContext(
        group=str(group or "").lower(),
        basis=str(basis or ""),
        reported_rate=reported_rate,
        effective_rate=float(effective_rate),
        amount_derived_rate=float(amount_derived_rate),
        legal_floor_rate=legal_floor_rate,
        rate_mismatch_ratio=rate_mismatch_ratio,
        floor_applicability=floor_applicability,
        reserve_price_count=max(0, int(reserve_price_count or 0)),
    )
    flags = tuple(flag for flag, predicate in _QUALITY_RULES if predicate(ctx))
    floor_undercut = (
        round(legal_floor_rate - ctx.reported_rate, 6)
        if FLAG_BELOW_LEGAL_FLOOR in flags
        and legal_floor_rate is not None
        and ctx.reported_rate is not None
        else None
    )
    return QualityAssessment(
        flags=flags,
        legal_floor_rate=legal_floor_rate,
        legal_floor_source=legal_floor_source,
        rate_mismatch_ratio=(
            round(rate_mismatch_ratio, 6) if rate_mismatch_ratio is not None else None
        ),
        floor_undercut=floor_undercut,
        floor_applicability=floor_applicability,
        published_floor_implausible=floor.published_floor_implausible,
        rate_basis_unverified=_is_floor_verdict_rate_basis_blocked(ctx),
        reserve_price_count=ctx.reserve_price_count,
    )
