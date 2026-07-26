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
    PUBLISHED_FLOOR_MAX_PLAUSIBLE,
    PUBLISHED_FLOOR_MIN_PLAUSIBLE,
    is_published_floor_plausible,
    resolve_floor_applicability,
)
from app.ai.holdout_quality import (
    FLAG_BELOW_LEGAL_FLOOR,
    FLOOR_SOURCE_ERA_TIER,
    FLOOR_SOURCE_NONE,
    FLOOR_SOURCE_PUBLISHED,
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
        # 국가·지자체 기관은 기본값 유지(기존 판정 그대로).
        ("산림청 북부지방산림청 홍천국유림관리소", FLOOR_APPLICABLE),
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
    """라이브 오탐 재현 기본값: 신율 시행 후 공사, era-tier 0.89745 를 하회하는 낙찰률."""
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
    }
    kwargs.update(overrides)
    return assess_row_quality(**kwargs)


def test_state_agency_keeps_existing_below_floor_verdict():
    """회귀 가드: 국가기관은 기존 판정을 그대로 유지한다(게이트가 전부를 끄지 않는다)."""
    assessment = _assess(agency_name="산림청 북부지방산림청 홍천국유림관리소")
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


# ── 리포트 노출(침묵 스킵 금지) ───────────────────────────────────────────────
def _row(applicability: str, *, implausible: bool = False) -> dict:
    return {
        "data_quality_details": {
            "floor_applicability": applicability,
            "published_floor_implausible": implausible,
        }
    }


def test_floor_applicability_report_counts_both_scopes():
    aggregated = [_row(FLOOR_APPLICABLE), _row(FLOOR_NOT_APPLICABLE)]
    evaluated = [
        *aggregated,
        _row(FLOOR_APPLICABILITY_UNCERTAIN),
        _row(FLOOR_NOT_APPLICABLE, implausible=True),
    ]
    report = build_floor_applicability_report(aggregated, evaluated_rows=evaluated)

    assert report["floor_applicability_counts"] == {
        FLOOR_APPLICABLE: 1,
        FLOOR_NOT_APPLICABLE: 1,
        FLOOR_APPLICABILITY_UNCERTAIN: 0,
    }
    assert report["evaluated_floor_applicability_counts"] == {
        FLOOR_APPLICABLE: 1,
        FLOOR_NOT_APPLICABLE: 2,
        FLOOR_APPLICABILITY_UNCERTAIN: 1,
    }
    assert report["published_floor_implausible_count"] == 0
    assert report["evaluated_published_floor_implausible_count"] == 1


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
