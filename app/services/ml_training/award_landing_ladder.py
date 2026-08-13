"""착지 코퍼스 품질 사다리 — 스칼라 한 벌 → 관측 1건 (순수 판정).

무엇을 고르는가
---------------
Phase 3 의 추정 대상은 개찰 1건의 ``(φ, ω)`` 두 값뿐이다(설계 §3.1). 그 두 값이
**근거 있게** 성립하는 행만 남기는 것이 이 모듈의 전부다. 판정 산술은 전부 PR1 순수
커널(:mod:`app.domain.award_margin_distribution`)이 하고, 여기서 통계를 다시 구현하지
않는다(§4.5-8).

사다리는 **신설하지 않고 재사용**한다(설계 §4). 각 단계는 이미 이 저장소가 소유한
단일 출처 술어를 그대로 부른다:

======================  =====================================================
단계                     출처
======================  =====================================================
게시 하한 개연           :func:`app.domain.published_floor_rate.plausible_published_floor_rate`
적용범위(기관 축)        :func:`app.ai.floor_applicability.is_floor_judgeable` (#274/#296)
낙찰가 존재              ``winning_amount > 0`` — 미정산은 NULL 이 **아니라 0.0** 이다
낙찰률 유효 창           :func:`app.domain.rate_normalization.is_plausible_rate_label`
basis clean              :func:`app.domain.reliable_base.get_reliable_base` (#199/#358)
예비가 보유              :data:`app.ai.holdout_quality.MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE`
사정률 개연              :func:`app.domain.floor_shortfall.is_plausible_assessment_rate`
레짐 게이트              :data:`REGIME_GATE_POLICIES` (수의/near_100 은 마진 정의가 없다)
======================  =====================================================

탈락은 **침묵하지 않는다**: :class:`LadderStage` 로 사유를 돌려주고, 게이트 전 레짐
라벨과 음수 마진 감사는 탈락한 행에서도 살아 나간다.

``ω`` 를 왜 직접 재계산하는가 (n5)
-----------------------------------
``app/services/paper_bidding_backtest/settlement.py:49-52`` 의 주석은 ``winning_rate``
를 base-relative 로 단언하고 결측 시 ``base_amount`` 로 폴백하지만, **실측은 예정가-basis**
다(설계 §11-3·n5). 그래서 이 사다리는 그 폴백을 쓰지 않고::

    ω = winning_amount / B(clean)          # 기초-basis, 직접 재계산
    w = winning_rate                        # 예정가-basis, 정규화만
    a = ω / w                               # 사정률 = 예정가/기초금액

로 세 축을 만든다. ``a`` 를 이 비로 얻는 것은 :func:`app.services.floor_shortfall.
assessment_rate_from_opening` 와 같은 도출식이며, 커널이 ``φ = f·a`` · ``ω = w·a`` 를
**같은 ``a``** 로 만들도록 셋을 한 관측 객체에 담는다(그쪽 docstring 의 계약).

라벨은 게시 ``f`` 로 재계산한다 (M4)
-------------------------------------
저장 ``would_have_won_final`` 라벨의 하한은 ``_resolve_floor_bid_rate(category)``(≈0.87
상수)×예정가라 게시 ``award_floor_rate`` 와 **실측 100% 불일치**한다. 이 코퍼스는 게시
``f`` 만 담고, 저장 라벨과의 대조는 리포트의 "불일치 실측" 항목이 따로 한다.

순수 함수(I/O 0) — ORM 행이 아니라 스칼라만 받으므로 DB 없이 값 테이블로 검증된다(§4.7-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from app.ai.floor_applicability import is_floor_judgeable, resolve_floor_applicability
from app.ai.holdout_quality import MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE
from app.ai.predictors import PricePredictionContext
# 금액대 버킷·레짐 라벨은 라이브 메타데이터가 쓰는 것과 **같은 한 벌**이다(§4.5-8).
# 설계 §4 의 셀 깊이 표(lt_10m … gte_1b)가 이 버킷으로 측정됐으므로 여기서 재선언하면
# 리포트 수치와 설계 수치가 같은 축이 아니게 되고, 레짐 규칙을 다시 쓰면 #312 의 공사
# 수의견적 가드 같은 라이브 판정이 백테스트에서만 조용히 달라진다.
from app.ai.price_prediction.price_regime import (
    _amount_bucket,
    _build_price_regime_features,
)
from app.ai.predictors.legal_floor_spec import CONSTRUCTION_FLOOR_REVISION_DATE
from app.core.time import ensure_utc
from app.domain.award_margin_distribution import LandingObservation
from app.domain.floor_shortfall import is_plausible_assessment_rate
from app.domain.published_floor_rate import plausible_published_floor_rate
from app.domain.rate_normalization import is_plausible_rate_label, to_bid_rate_fraction
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.utils.numeric import optional_float
from app.utils.sequence_coercion import coerce_numeric_list

__all__ = [
    "AMBIGUOUS_REGIME_LABEL",
    "DEFAULT_REGIME_GATE",
    "ERA_TIER_POST_REVISION",
    "ERA_TIER_PRE_REVISION",
    "FLOOR_RATE_UNITY",
    "REGIME_GATE_POLICIES",
    "LadderStage",
    "LandingCandidate",
    "LandingRow",
    "LandingRowOutcome",
    "NegativeMarginAudit",
    "build_landing_row",
    "resolve_era_tier",
    "resolve_regime_label",
]

# 하한율이 정확히 1.0 인 오적재(설계 §4 m1: ``Δ<0`` 실측 4/2,152 건이 전부 이 값)를
# 개연 밴드 밖 일반 탈락과 **따로** 센다. 밴드(``PUBLISHED_FLOOR_MAX_PLAUSIBLE``=0.995)가
# 이미 이 값을 거르지만, 두 사유를 한 칸에 뭉치면 "얼마나 오적재인가"를 리포트가 잃는다.
FLOOR_RATE_UNITY: Final[float] = 1.0

# 분류기가 어떤 규칙도 확정하지 못했을 때의 fallback 라벨. 대조군 정책과 셀 리포트의
# ``ambiguous_share`` 가 같은 문자열을 봐야 하므로 상수로 둔다.
AMBIGUOUS_REGIME_LABEL: Final[str] = "ambiguous"

# 레짐 게이트 정책 — 코드 분기가 아니라 **선언 데이터**다(§4.5-3). 새 정책 = 여기 한 줄.
#
# ``floor_bound`` 가 설계 충실 기본값이다(§2: 수의(견적)는 개찰 1위 확정이라 하한 경쟁이
# 없어 마진 정의 자체가 성립하지 않는다).
#
# ``not_negotiated`` 는 **측정용 대조군**이고, 그것이 무엇을 회수하는지 정확히 적어 둔다:
# ``ambiguous`` 는 "텍스트가 없는 공고"가 **아니다**(실측 714행 전부 제목·본문 보유).
# 분류기가 규칙표의 어느 신호도 세우지 못해 fallback 으로 떨어진 행, 즉 **floor_bound
# 로 확정하지 못한 행**이다. 그래서 이 정책은 "가격경쟁이 아닌 것을 뺀다"가 아니라
# "가격경쟁이라고 **확정되지 않은** 것을 넣는다"는 **더 강한 포함 가정**을 얹는다.
#
# 그 가정을 지지하는 실측은 있지만 증명은 아니다: ``ambiguous`` 행의 마진 중앙값이
# ``floor_bound`` 행과 유사하다(공사 0.00583 vs 0.00528) — 하한 위 경쟁이 실제로
# 있었다면 기대되는 모양이다. 반대로 수의(near_100)라면 마진 정의 자체가 성립하지
# 않는다. 두 정책의 수치를 나란히 놓는 이유는 이 가정의 크기를 리포트가 드러내기
# 위함이고, 셀별 ``ambiguous_share`` 가 그 크기를 셀 단위로 신고한다.
REGIME_GATE_POLICIES: Final[dict[str, frozenset[str]]] = {
    "floor_bound": frozenset({"floor_bound"}),
    "not_negotiated": frozenset({"floor_bound", AMBIGUOUS_REGIME_LABEL}),
}
DEFAULT_REGIME_GATE: Final[str] = "floor_bound"

# era tier 어휘 — #197/#198 구율/신율 경계(공사 적격심사 +2%p 개정 시행일)를 그대로 쓴다.
# 이 축을 셀 키에 넣는 목적은 식 3 의 f-재기준이 f-이질 행을 가로질러 pool 하지 않게
# 하는 것이다(설계 NEW-4). **동질화에 실제로 성공하는지는 가정이 아니라 측정 대상**
# 이므로 리포트가 셀 내 f 분포를 그대로 싣는다(F1 판정 재료).
ERA_TIER_PRE_REVISION: Final[str] = "pre_2026_01_30"
ERA_TIER_POST_REVISION: Final[str] = "post_2026_01_30"


class LadderStage(Enum):
    """품질 사다리의 탈락 사유 — **선언 순서가 곧 적용 순서**다.

    한 행이 여러 사유에 걸릴 수 있으므로 계수를 "행 수의 분해"로 읽으려면 순서가 고정
    돼야 한다. 첫 탈락 사유 하나만 세고 나머지는 평가하지 않는다.
    """

    FLOOR_RATE_MISSING = "floor_rate_missing"
    FLOOR_RATE_UNITY = "floor_rate_unity"
    FLOOR_RATE_IMPLAUSIBLE = "floor_rate_implausible"
    FLOOR_NOT_APPLICABLE = "floor_not_applicable"
    WINNING_AMOUNT_MISSING = "winning_amount_missing"
    WINNING_RATE_UNUSABLE = "winning_rate_unusable"
    BASE_NOT_CLEAN = "base_not_clean"
    RESERVE_PRICES_MISSING = "reserve_prices_missing"
    ASSESSMENT_IMPLAUSIBLE = "assessment_implausible"
    REGIME_EXCLUDED = "regime_excluded"
    NEGATIVE_MARGIN = "negative_margin"


@dataclass(frozen=True)
class LandingRow:
    """사다리를 통과한 개찰 1건 — 커널 관측 + 셀 배정 + 감사용 원값.

    ``observation`` 이 커널이 먹는 유일한 형태이고, 나머지 필드는 진단(상관·f 분포·
    금액대 재현)과 리포트 회계용이다. ``omega_direct`` 는 ``winning_amount/B`` 직접값
    으로, 커널이 ``w·a`` 로 만드는 ω 와 **대조용**이다(커널 docstring 의 계약).
    """

    project_id: int
    opened_at: datetime
    category: str
    era_tier: str
    amount_band: str
    agency: str
    regime_label: str
    base_amount: float
    winning_amount: float
    floor_rate: float
    winning_rate: float
    assessment_ratio: float
    omega_direct: float

    @property
    def observation(self) -> LandingObservation:
        return LandingObservation(
            floor_rate=self.floor_rate,
            winning_rate=self.winning_rate,
            assessment_ratio=self.assessment_ratio,
        )

    @property
    def margin(self) -> float:
        """Δ = w − f (예정가-basis)."""
        return self.winning_rate - self.floor_rate


@dataclass(frozen=True)
class NegativeMarginAudit:
    """``Δ = w − f < 0`` 한 건 — 게시 하한 개연 게이트를 **적용하기 전** 값.

    두 질문의 증거다. ①설계 §4 m1 의 "``Δ<0`` 은 전부 ``award_floor_rate == 1.0``"이
    이 코퍼스에서도 성립하는가(``floor_rate_fraction`` 로 확인). ②커널
    ``NEGATIVE_MARGIN_EPSILON`` 을 넓혀도 되는가 — 넓힐 근거는 크기 분포가 양자화 규모
    (~1e-5)에 몰려 있을 때뿐이고, basis 오염 규모(≥1e-3)가 섞여 있으면 넓히는 순간
    진짜 오염까지 0+ 스파이크로 위장된다(PR2 백로그 ⑥).
    """

    project_id: int
    floor_rate_fraction: float
    winning_rate: float
    margin: float

    @property
    def magnitude(self) -> float:
        return -self.margin


@dataclass(frozen=True)
class LandingRowOutcome:
    """행 하나의 사다리 판정 — 통과분·탈락 사유·게이트 전 진단을 함께 실어 나른다.

    ``regime_label`` 과 ``negative_margin`` 은 **행이 탈락해도** 채워질 수 있다. 게이트
    전 레짐 분포와 음수 마진 크기 분포는 탈락한 행에서만 관측되는 값이라, 탈락과 함께
    버리면 "왜 코퍼스가 이만큼인가"를 리포트가 되물을 수 없다.
    """

    row: LandingRow | None
    stage: LadderStage | None
    regime_label: str | None
    negative_margin: NegativeMarginAudit | None


@dataclass(frozen=True)
class LandingCandidate:
    """사다리에 들어가는 **원 스칼라 한 벌** — DB 행 shape 과 판정 계약을 가르는 지점.

    쿼리 Row 가 아니라 이 값 객체를 받으므로 사다리 전체가 DB 없이 값 테이블로 검증
    된다(§4.7-4). 필드 이름이 곧 "무엇을 읽는가"의 계약이다.
    """

    project_id: int
    opened_at: datetime
    category: str | None
    agency_name: str | None
    regime_text: str
    award_floor_rate: float | None
    winning_amount: float | None
    winning_rate: float | None
    base_amount: float | None
    base_amount_basis: str | None
    base_amount_estimated: float | None
    reserve_prices: str | None


@dataclass(frozen=True)
class _Axes:
    """금액 축 세 값 — 사다리 중간 산물(B, 낙찰가, ω, a)."""

    base_amount: float
    winning_amount: float
    omega_direct: float
    assessment_ratio: float


def resolve_era_tier(opened_at: datetime) -> str:
    """개찰 시각 → era tier(구율/신율). 경계는 시행일 **포함**이 신율이다."""
    return (
        ERA_TIER_POST_REVISION
        if ensure_utc(opened_at).date() >= CONSTRUCTION_FLOOR_REVISION_DATE
        else ERA_TIER_PRE_REVISION
    )


def resolve_regime_label(
    *, category: str | None, text: str, budget: float, floor_rate: float | None
) -> str:
    """공고 텍스트에서 가격 레짐 라벨을 얻는다 — **라이브 메타데이터 경로 그대로**.

    ``_build_price_regime_features`` 를 직접 부르는 것이 요점이다. 규칙표만 빌려 와
    매칭을 다시 쓰면 #312 공사 수의견적 가드처럼 "라벨만 바꾸는" 후속 변경이 백테스트
    코퍼스에 반영되지 않고 조용히 갈린다.

    ``floor_rate`` 는 게시 하한이 해석됐다는 증거로 넘긴다(그 가드의 입력).
    """
    features = _build_price_regime_features(
        {"legal_floor_bid_rate": floor_rate},
        context=PricePredictionContext(
            budget=budget,
            category=str(category or ""),
            description=text,
            historical_records=(),
        ),
    )
    return str(features["price_regime_label"])


def _plausible_reported_rate(winning_rate: float | None) -> float | None:
    """보고 낙찰률 → 유효 창 안의 fraction. 밖이면 ``None``."""
    raw = optional_float(winning_rate)
    if raw is None or raw <= 0.0:
        return None
    reported = to_bid_rate_fraction(raw)
    return reported if is_plausible_rate_label(reported) else None


def _negative_margin_audit(
    *, project_id: int, floor_rate_fraction: float, reported_rate: float | None
) -> NegativeMarginAudit | None:
    """게이트 전 ``Δ = w − f`` 가 음수일 때만 감사 레코드를 만든다."""
    if reported_rate is None:
        return None
    margin = reported_rate - floor_rate_fraction
    if margin >= 0.0:
        return None
    return NegativeMarginAudit(
        project_id=project_id,
        floor_rate_fraction=floor_rate_fraction,
        winning_rate=reported_rate,
        margin=margin,
    )


def _gate_floor(
    floor_rate_fraction: float, agency_name: str | None
) -> tuple[float | None, LadderStage | None]:
    """하한 축 게이트 — 단위값 오적재 · 개연 밴드 · 기관 적용범위."""
    if floor_rate_fraction == FLOOR_RATE_UNITY:
        return None, LadderStage.FLOOR_RATE_UNITY
    floor_rate = plausible_published_floor_rate(floor_rate_fraction)
    if floor_rate is None:
        return None, LadderStage.FLOOR_RATE_IMPLAUSIBLE
    if not is_floor_judgeable(resolve_floor_applicability(agency_name)):
        return None, LadderStage.FLOOR_NOT_APPLICABLE
    return floor_rate, None


def _gate_axes(
    candidate: LandingCandidate, *, reported_rate: float | None
) -> tuple[_Axes | None, LadderStage | None]:
    """금액 축 게이트 — 낙찰가·낙찰률·clean basis·예비가·사정률 개연."""
    award_amount = optional_float(candidate.winning_amount)
    if award_amount is None or award_amount <= 0.0:
        # 미정산은 NULL 이 아니라 0.0 으로 적재된다(설계 §4 함정).
        return None, LadderStage.WINNING_AMOUNT_MISSING
    if reported_rate is None:
        return None, LadderStage.WINNING_RATE_UNUSABLE

    reliable = get_reliable_base(
        candidate.base_amount,
        candidate.base_amount_basis,
        candidate.base_amount_estimated,
    )
    if reliable.source is not ReliableBaseSource.CLEAN_BASE or reliable.value is None:
        # 복구 추정치(reserve-estimate)는 추정 오차를 분포의 꼬리에 직접 싣는다 —
        # floor_shortfall 의 표본 규칙과 같은 스탠스로 여기서도 clean 만 받는다.
        return None, LadderStage.BASE_NOT_CLEAN
    reserve_count = len(coerce_numeric_list(candidate.reserve_prices))
    if reserve_count < MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE:
        # 예비가가 없으면 보고 낙찰률이 금액비 파생일 수 있고, 그러면 사정률이 1 로
        # 붕괴해 φ 와 ω 가 같은 축으로 접힌다.
        return None, LadderStage.RESERVE_PRICES_MISSING

    reliable_base = float(reliable.value)
    omega_direct = award_amount / reliable_base
    assessment_ratio = omega_direct / reported_rate
    if not is_plausible_assessment_rate(assessment_ratio):
        return None, LadderStage.ASSESSMENT_IMPLAUSIBLE
    return (
        _Axes(
            base_amount=reliable_base,
            winning_amount=award_amount,
            omega_direct=omega_direct,
            assessment_ratio=assessment_ratio,
        ),
        None,
    )


def build_landing_row(
    candidate: LandingCandidate, *, regime_labels: frozenset[str]
) -> LandingRowOutcome:
    """후보 스칼라 한 벌 → :class:`LandingRowOutcome`. 순수 함수(§4.7-4).

    사다리는 :class:`LadderStage` 의 **선언 순서대로** 적용하고 첫 탈락에서 멈춘다.
    음수 마진 감사가 게시 하한 개연 게이트보다 먼저 계산되는 이유는 모듈 docstring 참조.
    """
    raw_floor = optional_float(candidate.award_floor_rate)
    if raw_floor is None or raw_floor <= 0.0:
        return LandingRowOutcome(None, LadderStage.FLOOR_RATE_MISSING, None, None)
    floor_rate_fraction = to_bid_rate_fraction(raw_floor)
    reported_rate = _plausible_reported_rate(candidate.winning_rate)
    audit = _negative_margin_audit(
        project_id=candidate.project_id,
        floor_rate_fraction=floor_rate_fraction,
        reported_rate=reported_rate,
    )

    floor_rate, stage = _gate_floor(floor_rate_fraction, candidate.agency_name)
    if floor_rate is None:
        return LandingRowOutcome(None, stage, None, audit)
    axes, stage = _gate_axes(candidate, reported_rate=reported_rate)
    if axes is None or reported_rate is None:
        return LandingRowOutcome(None, stage, None, audit)

    label = resolve_regime_label(
        category=candidate.category,
        text=candidate.regime_text,
        budget=axes.base_amount,
        floor_rate=floor_rate,
    )
    stage = _gate_regime(
        label, regime_labels, reported_rate=reported_rate, floor_rate=floor_rate
    )
    if stage is not None:
        return LandingRowOutcome(None, stage, label, audit)
    row = _landing_row(
        candidate,
        label=label,
        floor_rate=floor_rate,
        reported_rate=reported_rate,
        axes=axes,
    )
    return LandingRowOutcome(row, None, label, audit)


def _gate_regime(
    label: str,
    regime_labels: frozenset[str],
    *,
    reported_rate: float,
    floor_rate: float,
) -> LadderStage | None:
    """레짐 게이트 + 음수 마진 거부 — 라벨을 안 뒤에야 판정할 수 있는 두 단계."""
    if label not in regime_labels:
        return LadderStage.REGIME_EXCLUDED
    if reported_rate < floor_rate:
        # 하한 아래 낙찰은 도메인상 불가능하다 — clamp 하지 않고 거부한다(커널의
        # ``extract_margins`` 와 같은 스탠스). 여기서 미리 거부해야 셀 배정·상관 진단이
        # 음수 마진 행을 섞지 않고, 크기 분포는 ``negative_margin`` 이 따로 실어 나른다.
        return LadderStage.NEGATIVE_MARGIN
    return None


def _landing_row(
    candidate: LandingCandidate,
    *,
    label: str,
    floor_rate: float,
    reported_rate: float,
    axes: _Axes,
) -> LandingRow:
    return LandingRow(
        project_id=candidate.project_id,
        opened_at=ensure_utc(candidate.opened_at),
        category=str(candidate.category or ""),
        era_tier=resolve_era_tier(candidate.opened_at),
        amount_band=_amount_bucket(axes.base_amount),
        agency=str(candidate.agency_name or ""),
        regime_label=label,
        base_amount=axes.base_amount,
        winning_amount=axes.winning_amount,
        floor_rate=floor_rate,
        winning_rate=reported_rate,
        assessment_ratio=axes.assessment_ratio,
        omega_direct=axes.omega_direct,
    )
