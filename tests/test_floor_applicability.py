"""법정 하한 적용 범위 판별 값 테이블 + 하회 판정 스킵 배선 테스트.

전부 순수 함수라 DB·예측·네트워크 없이 돈다(§4.7). 케이스는 라이브 기준선
(``--group-by agency`` 2,798건, 2026-07-26)에서 ``below_legal_floor`` 로 잡힌 실제
기관명을 그대로 쓴다 — 오탐 회귀 가드가 실데이터에 붙어 있어야 한다.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.ai.floor_applicability import (
    _AGENCY_PATTERNS,
    ALL_FLOOR_APPLICABILITIES,
    FLOOR_APPLICABILITY_UNCERTAIN,
    FLOOR_APPLICABLE,
    FLOOR_NOT_APPLICABLE,
    FLOOR_SEPARATE_REGIME,
    FORESTRY_REGIME_FLOOR_RATE,
    PUBLISHED_FLOOR_MAX_PLAUSIBLE,
    PUBLISHED_FLOOR_MIN_PLAUSIBLE,
    is_published_floor_plausible,
    resolve_floor_applicability,
)
from app.ai.holdout_quality import (
    FLAG_AMOUNT_RATE_MISMATCH,
    FLAG_BELOW_LEGAL_FLOOR,
    FLAG_LOW_ACTUAL_RATE,
    FLOOR_SOURCE_ERA_TIER,
    FLOOR_SOURCE_FORESTRY,
    FLOOR_SOURCE_NONE,
    FLOOR_SOURCE_PUBLISHED,
    MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE,
    RATE_BASIS_INDEPENDENCE_TOLERANCE,
    assess_row_quality,
)
from app.ai.holdout_reporting import build_floor_applicability_report
from app.services.base_amount_basis import BASIS_CLEAN


# ── 기관 유형 판별(값 테이블) ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "agency,expected",
    [
        # 비국가기관 — 라이브 실측 기관명.
        ("한동대학교 산학협력단", FLOOR_NOT_APPLICABLE),
        ("울산과학대학산학협력단", FLOOR_NOT_APPLICABLE),
        ("경북과학대학교산학협력단", FLOOR_NOT_APPLICABLE),
        ("청천농업협동조합", FLOOR_NOT_APPLICABLE),
        ("군위축산업협동조합", FLOOR_NOT_APPLICABLE),
        ("충북인삼협동조합", FLOOR_NOT_APPLICABLE),
        ("축협중앙회 담양축산업협동조합", FLOOR_NOT_APPLICABLE),
        ("학교법인 한국항공대학", FLOOR_NOT_APPLICABLE),
        ("농협중앙회", FLOOR_NOT_APPLICABLE),
        ("청천농협", FLOOR_NOT_APPLICABLE),
        ("부산 수협", FLOOR_NOT_APPLICABLE),
        # 이름만으로 국공립/사립을 가를 수 없는 부류 — 라이브 실측.
        ("울산대학교", FLOOR_APPLICABILITY_UNCERTAIN),
        ("명지전문대학", FLOOR_APPLICABILITY_UNCERTAIN),
        ("경북보건대학교", FLOOR_APPLICABILITY_UNCERTAIN),
        ("계원예술대학교", FLOOR_APPLICABILITY_UNCERTAIN),
        # 산림사업 별도 행정규칙 계열 — 라이브 실측 6건이 이 표기들에 걸린다.
        ("산림청 서부지방산림청 영암국유림관리소", FLOOR_SEPARATE_REGIME),
        ("산림청 북부지방산림청 홍천국유림관리소", FLOOR_SEPARATE_REGIME),
        ("산림청 국립산림품종관리센터", FLOOR_SEPARATE_REGIME),
        ("산림청 산림항공본부 익산산림항공관리소", FLOOR_SEPARATE_REGIME),
        ("남부지방산림청", FLOOR_SEPARATE_REGIME),
        ("영암국유림관리소", FLOOR_SEPARATE_REGIME),
        ("산림청", FLOOR_SEPARATE_REGIME),
        # 국가·지자체 기관은 기본값 유지(기존 판정 그대로).
        ("한국농어촌공사 충북지역본부 청주지사", FLOOR_APPLICABLE),
        ("울산광역시 울주군", FLOOR_APPLICABLE),
        ("조달청", FLOOR_APPLICABLE),
        # 기관 미상은 판정을 넓게 생략하지 않는다(기본값 applicable).
        ("", FLOOR_APPLICABLE),
        (None, FLOOR_APPLICABLE),
    ],
)
def test_resolve_floor_applicability_value_table(agency, expected):
    assert resolve_floor_applicability(agency) == expected


def test_not_applicable_beats_uncertain_for_university_foundations():
    """'대학교 산학협력단'은 판별 불가가 아니라 명백한 비국가기관이다(테이블 순서)."""
    assert resolve_floor_applicability("울산대학교 산학협력단") == FLOOR_NOT_APPLICABLE
    assert resolve_floor_applicability("인제대학교 산학협력단") == FLOOR_NOT_APPLICABLE


def test_pattern_table_orders_not_applicable_before_uncertain():
    """첫 매칭이 이기므로 not_applicable 항목이 uncertain 앞에 있어야 한다."""
    labels = [pattern.applicability for pattern in _AGENCY_PATTERNS]
    first_uncertain = labels.index(FLOOR_APPLICABILITY_UNCERTAIN)
    assert FLOOR_NOT_APPLICABLE not in labels[first_uncertain:]


@pytest.mark.parametrize(
    "agency",
    ["농업용수협의체", "치수협의회", "한국농어촌공사 농협업무지원단"],
)
def test_short_cooperative_abbreviations_do_not_match_mid_name(agency):
    """회귀 가드: 짧은 약칭(수협/농협)이 무관한 기관명 substring 으로 걸리면 안 된다."""
    assert resolve_floor_applicability(agency) == FLOOR_APPLICABLE


@pytest.mark.parametrize(
    "agency",
    [
        "강릉시산림조합",
        "산림조합중앙회",
        "산림조합중앙회 진주시산림조합",
        "경상북도 산림환경연구원 북부지원",
        "한국산림복지진흥원",
    ],
)
def test_forestry_cooperatives_are_not_separate_regime(agency):
    """회귀 가드: 산림**조합**·산림 유관기관은 산림청 계열이 아니다(토큰 미포함)."""
    assert resolve_floor_applicability(agency) == FLOOR_APPLICABLE


def test_whitespace_variants_resolve_to_the_same_verdict():
    for variant in ("인제대학교 산학협력단", "인제대학교산학협력단", " 인제대학교  산학협력단 "):
        assert resolve_floor_applicability(variant) == FLOOR_NOT_APPLICABLE


# ── 게시 하한율 개연 범위 ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "rate,expected",
    [
        (PUBLISHED_FLOOR_MIN_PLAUSIBLE, True),  # 경계 포함
        (PUBLISHED_FLOOR_MAX_PLAUSIBLE, True),  # 경계 포함
        (PUBLISHED_FLOOR_MIN_PLAUSIBLE - 0.0001, False),
        (PUBLISHED_FLOOR_MAX_PLAUSIBLE + 0.0001, False),
        (1.0, False),  # 라이브 실측 이상값(예정가 전액 이상 투찰은 하한이 아니다)
        (0.47995, True),  # 라이브 실측 최저 실값 — 버리면 새 오탐이 생긴다
        (0.89745, True),
        (0.0088, False),  # 스케일 오적재
        (None, False),
    ],
)
def test_is_published_floor_plausible_boundaries(rate, expected):
    assert is_published_floor_plausible(rate) is expected


# ── 하회 판정 배선 ───────────────────────────────────────────────────────────
def _assess(**overrides):
    """라이브 오탐 재현 기본값: 신율 시행 후 공사, era-tier 0.89745 를 하회하는 낙찰률.

    기본값은 복수예비가격 15개(=예정가를 독립 재구성할 수 있는 행)를 가정한다. 그래야
    보고율이 금액비와 같아도 rate-basis 게이트에 걸리지 않아, 이 블록의 케이스들이
    **적용 범위 축만** 검증한다.
    """
    kwargs = {
        "group": "construction",
        "category": "construction",
        "basis": BASIS_CLEAN,
        "reported_rate": 0.70,
        "effective_rate": 0.70,
        "amount_derived_rate": 0.70,
        "published_floor_rate": None,
        "estimation_amount": 500_000_000.0,
        "reference_date": date(2026, 2, 1),
        "agency_name": None,
        "reserve_price_count": 15,
    }
    kwargs.update(overrides)
    return assess_row_quality(**kwargs)


def test_state_agency_keeps_existing_below_floor_verdict():
    """회귀 가드: 국가기관은 기존 판정을 그대로 유지한다(게이트가 전부를 끄지 않는다)."""
    assessment = _assess(agency_name="한국농어촌공사 충북지역본부 청주지사")
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.floor_applicability == FLOOR_APPLICABLE
    assert assessment.legal_floor_source == FLOOR_SOURCE_ERA_TIER


def test_missing_agency_name_preserves_legacy_behaviour():
    assessment = _assess()
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.floor_applicability == FLOOR_APPLICABLE


def test_non_state_agency_skips_below_floor_but_keeps_the_resolved_floor():
    """산학협력단 공고에 국가계약 era-tier 를 적용한 오탐(라이브 10건)을 막는다."""
    assessment = _assess(agency_name="인제대학교 산학협력단", reported_rate=0.67086)
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.floor_applicability == FLOOR_NOT_APPLICABLE
    assert assessment.floor_undercut is None
    # 해석된 하한 자체는 남는다 — 판정만 생략했다는 사실이 추적 가능해야 한다.
    assert assessment.legal_floor_rate == pytest.approx(0.89745)
    assert assessment.as_details()["floor_applicability"] == FLOOR_NOT_APPLICABLE


def test_cooperative_agency_skips_below_floor():
    assessment = _assess(agency_name="청천농업협동조합", reported_rate=0.43631)
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.floor_applicability == FLOOR_NOT_APPLICABLE


def test_uncertain_agency_skips_below_floor_and_records_the_reason():
    assessment = _assess(agency_name="울산대학교", reported_rate=0.79857)
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.floor_applicability == FLOOR_APPLICABILITY_UNCERTAIN


# ── 산림사업 별도 규정(하한 87.745%) ─────────────────────────────────────────
# 라이브 실측 6건이 걸린 계열의 대표 표기.
_FORESTRY_AGENCY = "산림청 서부지방산림청 영암국유림관리소"


def test_forestry_floor_rate_matches_the_verified_bid_notice_text():
    """원문 확정 가드: 산림사업 하한은 국가계약 +2%p 개정을 추종하지 않는다.

    근거는 산림사업 입찰설명서 원문(공고 R26BK01490237, 입찰 2026-04-27~05-06 —
    개정 시행일 **이후**)의 "예정가격의 87.745% 이상"과 산림청예규 제728호다. 예규가
    개정되면 이 상수와 이 테스트를 함께 갱신한다.
    """
    assert FORESTRY_REGIME_FLOOR_RATE == pytest.approx(0.87745)
    # 국가계약 신율(0.89745)을 그대로 대면 적법 낙찰이 하회로 잡히던 원인.
    assert FORESTRY_REGIME_FLOOR_RATE < 0.89745


def test_forestry_agency_is_judged_against_the_forestry_floor():
    """검출 능력 복원: 산림사업 하한을 진짜 하회하면 다시 잡힌다.

    헬퍼 기본 예비가 15개라 rate-basis 게이트는 열려 있고, 판정 축은 하한값 하나뿐이다.
    """
    reported = 0.87246
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=reported,
    )
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.floor_applicability == FLOOR_SEPARATE_REGIME
    assert assessment.legal_floor_rate == pytest.approx(FORESTRY_REGIME_FLOOR_RATE)
    assert assessment.legal_floor_source == FLOOR_SOURCE_FORESTRY
    assert assessment.floor_undercut == pytest.approx(
        FORESTRY_REGIME_FLOOR_RATE - reported, abs=1e-9
    )
    details = assessment.as_details()
    assert details["floor_applicability"] == FLOOR_SEPARATE_REGIME
    assert details["legal_floor_source"] == FLOOR_SOURCE_FORESTRY


def test_forestry_service_notice_keeps_the_floor_unresolved():
    """오탐 가드: 산림사업 하한은 **공사** 적격심사 값이라 용역 공고에 대지 않는다.

    용역 적심 하한은 공사보다 낮아, 0.86 같은 적법 낙찰에 0.87745 를 대면 하회로
    오탐된다. 근거가 확인된 축(원문·라이브 표본 전부 공사) 밖이므로 미해석으로 남긴다.
    """
    reported = 0.86
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        group="service",
        category="service",
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=reported,
    )
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.legal_floor_rate is None
    assert assessment.legal_floor_source == FLOOR_SOURCE_NONE
    assert assessment.floor_undercut is None
    # 기관 축 라벨 자체는 그대로 남는다 — 생략 사유가 카테고리 축이라는 게 드러나야 한다.
    assert assessment.floor_applicability == FLOOR_SEPARATE_REGIME
    # 하한 비교를 애초에 하지 않았으므로 rate-basis 생략 건수에도 잡히지 않는다.
    assert assessment.rate_basis_unverified is False


@pytest.mark.parametrize("category", ["construction", "공사"])
def test_forestry_floor_reuses_the_shared_category_normalization(category):
    """공사 게이트는 era-tier 와 같은 정규화를 쓴다 — 한글 표기에서 갈리면 안 된다."""
    reported = 0.87246
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        category=category,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=reported,
    )
    assert assessment.legal_floor_source == FLOOR_SOURCE_FORESTRY
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags


def test_forestry_live_lowest_award_is_lawful_under_the_forestry_floor():
    """라이브 미러: 실측 하단 0.87766 은 산림사업 하한 바로 위 = 하회 아님.

    보고율은 예정가-basis 독립 실측(금액-역산율과 독립성 경계 밖으로 갈린다)이라
    예비가 없이도 판정이 수행된다 — 전건 적법이 재현돼야 한다.
    """
    reported = 0.87766
    derived = reported / (1 + 5 * RATE_BASIS_INDEPENDENCE_TOLERANCE)
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=derived,
        reserve_price_count=0,
    )
    assert reported > FORESTRY_REGIME_FLOOR_RATE
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.rate_basis_unverified is False
    assert assessment.floor_undercut is None
    assert assessment.legal_floor_source == FLOOR_SOURCE_FORESTRY
    # 독립성 경계와 분모 불일치 허용오차 사이라 다른 축 플래그도 붙지 않는다.
    assert FLAG_AMOUNT_RATE_MISMATCH not in assessment.flags


def test_forestry_rate_basis_gate_still_precedes_the_forestry_floor():
    """게이트 순서 가드: 보고율이 금액비 파생이면 산림사업 하한으로도 판정하지 않는다."""
    reported = 0.87246
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=reported,
        reserve_price_count=0,
    )
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.rate_basis_unverified is True
    assert assessment.floor_undercut is None
    # 해석된 하한·출처는 생략된 행에도 남는다(#274 추적성 패턴).
    assert assessment.legal_floor_rate == pytest.approx(FORESTRY_REGIME_FLOOR_RATE)
    assert assessment.as_details()["legal_floor_source"] == FLOOR_SOURCE_FORESTRY


def test_plausible_published_floor_beats_the_forestry_constant():
    """공고가 게시한 하한이 개연 범위 안이면 산림사업 상수보다 우선한다(우선순위 1)."""
    published = 0.88
    reported = 0.87246
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        published_floor_rate=published,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=reported,
    )
    assert assessment.legal_floor_source == FLOOR_SOURCE_PUBLISHED
    assert assessment.legal_floor_rate == pytest.approx(published)
    assert assessment.legal_floor_rate != pytest.approx(FORESTRY_REGIME_FLOOR_RATE)
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.floor_undercut == pytest.approx(published - reported, abs=1e-9)


def test_implausible_published_floor_falls_back_to_forestry_not_era_tier():
    """게시값이 이상값이어도 산림청 계열의 폴백은 era-tier 가 아니라 산림사업 하한이다."""
    reported = 0.88
    assessment = _assess(
        agency_name=_FORESTRY_AGENCY,
        published_floor_rate=1.0,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=reported,
    )
    assert assessment.published_floor_implausible is True
    assert assessment.legal_floor_source == FLOOR_SOURCE_FORESTRY
    assert assessment.legal_floor_rate == pytest.approx(FORESTRY_REGIME_FLOOR_RATE)
    # 0.88 은 산림사업 하한 위 — era-tier(0.89745)를 잘못 소환했다면 하회로 잡혔을 값.
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags


def test_separate_regime_is_a_distinct_label_from_not_applicable():
    """산림청은 국가기관이 맞다 — 비국가기관과 같은 버킷으로 접지 않는다."""
    assert FLOOR_SEPARATE_REGIME != FLOOR_NOT_APPLICABLE
    assert FLOOR_SEPARATE_REGIME in ALL_FLOOR_APPLICABILITIES
    assert resolve_floor_applicability("영암국유림관리소") == FLOOR_SEPARATE_REGIME
    assert resolve_floor_applicability("청천농업협동조합") == FLOOR_NOT_APPLICABLE


def test_implausible_published_floor_falls_back_to_era_tier():
    """게시 하한율 1.00000(라이브 실측)은 판정에 쓰지 않고 era-tier 로 폴백한다."""
    assessment = _assess(
        agency_name="울산광역시 울주군",
        published_floor_rate=1.0,
        reported_rate=0.95131,
        effective_rate=0.95131,
        amount_derived_rate=0.95131,
    )
    assert assessment.published_floor_implausible is True
    assert assessment.legal_floor_source == FLOOR_SOURCE_ERA_TIER
    assert assessment.legal_floor_rate == pytest.approx(0.89745)
    # 0.95131 은 era-tier 위 — 이상값 1.0 때문에 붙던 하회 플래그가 사라진다.
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.as_details()["published_floor_implausible"] is True


def test_implausible_published_floor_without_era_tier_leaves_floor_unresolved():
    assessment = _assess(
        agency_name="울산광역시 울주군",
        category="service",
        group="service",
        published_floor_rate=1.0,
        reported_rate=0.95131,
        effective_rate=0.95131,
        amount_derived_rate=0.95131,
    )
    assert assessment.published_floor_implausible is True
    assert assessment.legal_floor_rate is None
    assert assessment.legal_floor_source == FLOOR_SOURCE_NONE
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags


def test_plausible_published_floor_is_still_used_verbatim():
    """개연 범위 안의 게시값은 그대로 최우선이다(라이브 최저 실값 0.47995 포함)."""
    assessment = _assess(published_floor_rate=0.47995, agency_name="울산광역시 울주군")
    assert assessment.legal_floor_source == FLOOR_SOURCE_PUBLISHED
    assert assessment.legal_floor_rate == pytest.approx(0.47995)
    assert assessment.published_floor_implausible is False
    # 0.70 은 0.47995 위라 하회가 아니다 — era-tier 를 잘못 소환하지 않았다는 증거.
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags


def test_below_plausible_published_floor_still_flags():
    assessment = _assess(
        published_floor_rate=0.88,
        agency_name="울산광역시 울주군",
        reported_rate=0.70,
    )
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.floor_undercut == pytest.approx(0.18, abs=1e-9)


# ── 보고 낙찰률 basis 독립성 게이트 ───────────────────────────────────────────
_STATE_AGENCY = "울산광역시 울주군"


def test_amount_derived_reported_rate_without_reserves_skips_below_floor():
    """A군 재현(라이브 13건): 보고율이 금액비와 사실상 같고 예비가도 없으면 생략."""
    derived = 0.88123
    reported = derived * (1 + 1e-5)  # 실측 상대오차 상한
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=derived,
        reserve_price_count=0,
    )
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.rate_basis_unverified is True
    assert assessment.floor_undercut is None
    # 적용 범위 자체는 그대로다 — 생략 사유가 다른 축이라는 게 드러나야 한다.
    assert assessment.floor_applicability == FLOOR_APPLICABLE
    details = assessment.as_details()
    assert details["rate_basis_unverified"] is True
    assert details["reserve_price_count"] == 0
    assert details["rate_basis_independence_tolerance"] == RATE_BASIS_INDEPENDENCE_TOLERANCE


def test_exactly_equal_rates_without_reserves_skip_below_floor():
    """실측 6건은 상대오차가 정확히 0.0 이었다(금액비를 그대로 되돌려준 행)."""
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=0.88,
        effective_rate=0.88,
        amount_derived_rate=0.88,
        reserve_price_count=0,
    )
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert assessment.rate_basis_unverified is True


def test_independent_reported_rate_still_flags_below_floor():
    """회귀 가드: 독립 실측(상대오차 ≥1.4e-3)이면 진짜 하회는 계속 잡힌다."""
    derived = 0.70
    reported = derived * (1 + 1.4e-3)  # 독립 확인된 행들의 실측 최소 상대오차
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=derived,
        reserve_price_count=0,
    )
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.rate_basis_unverified is False
    assert assessment.floor_undercut is not None


def test_reserve_prices_keep_the_verdict_even_when_rates_agree():
    """사정률≈1 인 정상 케이스 보존: 예비가로 예정가를 독립 재구성할 수 있으면 판정 유지."""
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=0.70,
        effective_rate=0.70,
        amount_derived_rate=0.70,
        reserve_price_count=15,
    )
    assert FLAG_BELOW_LEGAL_FLOOR in assessment.flags
    assert assessment.rate_basis_unverified is False
    assert assessment.as_details()["reserve_price_count"] == 15


@pytest.mark.parametrize(
    "reserve_count,expected_flagged",
    [
        (MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE - 1, False),
        (MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE, True),  # 경계 포함
    ],
)
def test_reserve_price_count_boundary(reserve_count, expected_flagged):
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=0.70,
        effective_rate=0.70,
        amount_derived_rate=0.70,
        reserve_price_count=reserve_count,
    )
    assert (FLAG_BELOW_LEGAL_FLOOR in assessment.flags) is expected_flagged


@pytest.mark.parametrize(
    "offset,expected_flagged",
    [
        (RATE_BASIS_INDEPENDENCE_TOLERANCE - 1e-6, False),  # 경계 미만 = 파생 의심
        (RATE_BASIS_INDEPENDENCE_TOLERANCE + 1e-6, True),  # 경계 이상 = 독립 실측
    ],
)
def test_rate_basis_independence_tolerance_boundary(offset, expected_flagged):
    derived = 0.70
    reported = derived * (1 + offset)
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=reported,
        effective_rate=reported,
        amount_derived_rate=derived,
        reserve_price_count=0,
    )
    assert (FLAG_BELOW_LEGAL_FLOOR in assessment.flags) is expected_flagged
    assert assessment.rate_basis_unverified is not expected_flagged


def test_rate_basis_unverified_only_marks_rows_whose_verdict_it_skipped():
    """건수가 '이 게이트 때문에 생략된 판정'을 뜻하도록, 다른 사유로 생략된 행은 제외."""
    # 적용 범위 밖(비국가기관)이라 애초에 하한 비교를 하지 않는 행.
    non_state = _assess(
        agency_name="인제대학교 산학협력단",
        reported_rate=0.70,
        amount_derived_rate=0.70,
        reserve_price_count=0,
    )
    assert non_state.rate_basis_unverified is False
    # 하한 자체가 해석되지 않은 행(용역 + 게시값 없음).
    unresolved = _assess(
        group="service",
        category="service",
        agency_name=_STATE_AGENCY,
        reported_rate=0.70,
        amount_derived_rate=0.70,
        reserve_price_count=0,
    )
    assert unresolved.legal_floor_rate is None
    assert unresolved.rate_basis_unverified is False


def test_rate_basis_gate_does_not_touch_other_quality_flags():
    """게이트는 하한 판정만 막는다 — 분모 불일치 등 다른 축은 그대로 판정된다."""
    assessment = _assess(
        agency_name=_STATE_AGENCY,
        reported_rate=0.60,
        effective_rate=0.60,
        amount_derived_rate=0.60,
        reserve_price_count=0,
    )
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert FLAG_LOW_ACTUAL_RATE in assessment.flags


# ── 리포트 노출(침묵 스킵 금지) ───────────────────────────────────────────────
def _row(
    applicability: str,
    *,
    implausible: bool = False,
    rate_basis_unverified: bool = False,
) -> dict:
    return {
        "data_quality_details": {
            "floor_applicability": applicability,
            "published_floor_implausible": implausible,
            "rate_basis_unverified": rate_basis_unverified,
        }
    }


def test_floor_applicability_report_counts_both_scopes():
    aggregated = [_row(FLOOR_APPLICABLE), _row(FLOOR_NOT_APPLICABLE)]
    evaluated = [
        *aggregated,
        _row(FLOOR_APPLICABILITY_UNCERTAIN),
        _row(FLOOR_SEPARATE_REGIME),
        _row(FLOOR_NOT_APPLICABLE, implausible=True),
    ]
    report = build_floor_applicability_report(aggregated, evaluated_rows=evaluated)

    assert report["floor_applicability_counts"] == {
        FLOOR_APPLICABLE: 1,
        FLOOR_NOT_APPLICABLE: 1,
        FLOOR_APPLICABILITY_UNCERTAIN: 0,
        FLOOR_SEPARATE_REGIME: 0,
    }
    assert report["evaluated_floor_applicability_counts"] == {
        FLOOR_APPLICABLE: 1,
        FLOOR_NOT_APPLICABLE: 2,
        FLOOR_APPLICABILITY_UNCERTAIN: 1,
        FLOOR_SEPARATE_REGIME: 1,
    }
    assert report["published_floor_implausible_count"] == 0
    assert report["evaluated_published_floor_implausible_count"] == 1


def test_floor_applicability_report_counts_rate_basis_skips():
    """rate-basis 생략도 침묵하지 않는다(두 스코프 모두)."""
    aggregated = [_row(FLOOR_APPLICABLE, rate_basis_unverified=True)]
    evaluated = [*aggregated, _row(FLOOR_APPLICABLE, rate_basis_unverified=True)]
    report = build_floor_applicability_report(aggregated, evaluated_rows=evaluated)

    assert report["rate_basis_unverified_count"] == 1
    assert report["evaluated_rate_basis_unverified_count"] == 2


def test_floor_applicability_report_keeps_zero_labels_and_defaults_scope():
    report = build_floor_applicability_report([_row(FLOOR_APPLICABLE)])
    assert set(report["floor_applicability_counts"]) == set(ALL_FLOOR_APPLICABILITIES)
    assert (
        report["evaluated_floor_applicability_counts"]
        == report["floor_applicability_counts"]
    )


def test_floor_applicability_report_treats_missing_details_as_applicable():
    """상세가 없는 옛 행도 조용히 사라지지 않고 applicable 로 계상된다."""
    report = build_floor_applicability_report([{}, {"data_quality_details": None}])
    assert report["floor_applicability_counts"][FLOOR_APPLICABLE] == 2
