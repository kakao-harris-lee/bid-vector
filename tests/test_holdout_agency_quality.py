"""기관 축 홀드아웃 분할 + 분모/법정하한 품질 플래그 값 테이블 테스트.

전부 순수 함수라 DB·예측·네트워크 없이 돈다(§4.7). 리포트 섹션 빌더는 가짜
``aggregate_fn`` 을 주입해 검증한다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.ai.holdout_grouping import (
    AGENCY_ETC_KEY,
    AGENCY_UNKNOWN_KEY,
    bucket_small_agencies,
    normalize_agency_key,
    resolve_agency_group,
)
from app.ai.holdout_quality import (
    FLAG_AMOUNT_RATE_MISMATCH,
    FLAG_BASE_BASIS_CONTAMINATED,
    FLAG_BELOW_LEGAL_FLOOR,
    FLAG_CONSTRUCTION_LOW_RATE_REVIEW,
    FLAG_LOW_ACTUAL_RATE,
    FLAG_MISSING_AMOUNT_DERIVED_RATE,
    FLAG_MISSING_REPORTED_RATE,
    FLOOR_SOURCE_ERA_TIER,
    FLOOR_SOURCE_NONE,
    FLOOR_SOURCE_PUBLISHED,
    LEGAL_FLOOR_UNDERCUT_TOLERANCE,
    RATE_BASIS_INDEPENDENCE_TOLERANCE,
    RATE_MISMATCH_RELATIVE_TOLERANCE,
    assess_row_quality,
)
from app.ai.holdout_reporting import (
    build_agency_axis_report,
    build_quality_flag_report,
)
from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import BASIS_CLEAN, BASIS_DERIVED_YEGA
from app.services.prediction_dataset import PredictionDatasetService

# Load the script module by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "backtest_latest_award_holdouts_agency",
    Path(__file__).resolve().parents[1] / "scripts" / "backtest_latest_award_holdouts.py",
)
holdouts = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = holdouts
_SPEC.loader.exec_module(holdouts)


# ── 기관 키 정규화 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("울산광역시 울주군", "울산광역시울주군"),
        ("  울산광역시   울주군  ", "울산광역시울주군"),
        ("(주)해림종합건설", "해림종합건설"),
        ("주식회사 해림종합건설", "해림종합건설"),
        ("재단법인 한국항만협회", "한국항만협회"),
        ("Korea Ports  Authority", "koreaportsauthority"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_agency_key_value_table(raw, expected):
    assert normalize_agency_key(raw) == expected


def test_normalize_agency_key_collapses_corporate_prefix_variants():
    """같은 기관의 법인격 표기 흔들림이 하나의 분할 키로 모인다."""
    variants = ["(주)해림종합건설", "주식회사 해림종합건설", "㈜해림종합건설", "해림종합건설"]
    assert len({normalize_agency_key(name) for name in variants}) == 1


def test_normalize_agency_key_keeps_organization_type_suffix():
    """조직 종류 어미(공사/공단)는 서로 다른 기관을 가르는 정보라 제거하지 않는다."""
    assert normalize_agency_key("부산항만공사") != normalize_agency_key("부산항만공단")


# ── 기관 그룹 해석 ────────────────────────────────────────────────────────────
def test_resolve_agency_group_prefers_issuing_agency():
    group = resolve_agency_group("울산광역시 울주군", "울주군 건설과")
    assert group.key == "울산광역시울주군"
    assert group.display == "울산광역시 울주군"


def test_resolve_agency_group_falls_back_to_demand_agency():
    group = resolve_agency_group(None, "울주군 건설과")
    assert group.key == "울주군건설과"
    assert group.display == "울주군 건설과"


def test_resolve_agency_group_unknown_when_both_missing():
    group = resolve_agency_group("   ", None)
    assert group.key == AGENCY_UNKNOWN_KEY
    assert group.display == AGENCY_UNKNOWN_KEY


# ── 소표본 버킷 경계 ──────────────────────────────────────────────────────────
def test_bucket_small_agencies_min_sample_boundary():
    """표본 수 == min_samples 는 자기 버킷 유지, 하나 모자라면 _etc 로 합산."""
    keys = ["a", "a", "a", "b", "b", "c"]
    bucketing = bucket_small_agencies(keys, min_samples=3)
    assert bucketing.mapping["a"] == "a"
    assert bucketing.mapping["b"] == AGENCY_ETC_KEY
    assert bucketing.mapping["c"] == AGENCY_ETC_KEY
    assert bucketing.etc_agency_count == 2
    assert bucketing.etc_sample_count == 3
    assert bucketing.distinct_agency_count == 3
    assert bucketing.reported_agency_count == 1


def test_bucket_small_agencies_reports_folded_counts_not_silent_drop():
    """합산된 표본은 사라지지 않는다 — 버킷 총합이 입력 표본 수와 같다."""
    keys = ["a", "a", "a", "b", "c", "d"]
    bucketing = bucket_small_agencies(keys, min_samples=2)
    assert sum(bucketing.counts.values()) == len(keys)
    assert bucketing.etc_sample_count == 3  # b, c, d 각각 1건


def test_bucket_small_agencies_keeps_unknown_bucket_separate():
    """'기관 미상'과 '표본 부족'은 다른 사실이라 _unknown 은 _etc 로 접히지 않는다."""
    bucketing = bucket_small_agencies([AGENCY_UNKNOWN_KEY, "b"], min_samples=5)
    assert bucketing.mapping[AGENCY_UNKNOWN_KEY] == AGENCY_UNKNOWN_KEY
    assert bucketing.mapping["b"] == AGENCY_ETC_KEY


def test_bucket_small_agencies_blank_key_becomes_unknown():
    bucketing = bucket_small_agencies(["", None], min_samples=1)
    assert bucketing.counts == {AGENCY_UNKNOWN_KEY: 2}


def test_bucket_small_agencies_min_samples_floor_is_one():
    """0/음수 min_samples 는 1로 clamp — 전부 _etc 로 접히는 사고를 막는다."""
    bucketing = bucket_small_agencies(["a"], min_samples=0)
    assert bucketing.min_samples == 1
    assert bucketing.mapping["a"] == "a"


# ── 품질 플래그 ───────────────────────────────────────────────────────────────
def _assess(**overrides):
    """기본값은 복수예비가격 15개(예정가 독립 재구성 가능) 행이다.

    이 블록의 케이스들은 보고율과 금액-역산율을 일부러 같게 두고 **하한/분모 판정
    자체**를 검증한다. 예비가 증거가 없으면 rate-basis 게이트가 하한 판정을 먼저
    생략해 버려(``tests/test_floor_applicability.py`` 가 그 축을 따로 검증) 여기서
    보려는 축이 가려진다.
    """
    kwargs = {
        "group": "service",
        "category": "service",
        "basis": BASIS_CLEAN,
        "reported_rate": 0.90,
        "effective_rate": 0.90,
        "amount_derived_rate": 0.90,
        "published_floor_rate": None,
        "estimation_amount": None,
        "reference_date": None,
        "reserve_price_count": 15,
    }
    kwargs.update(overrides)
    return assess_row_quality(**kwargs)


def test_assess_clean_row_has_no_flags():
    assessment = _assess()
    assert assessment.flags == ()
    assert assessment.legal_floor_source == FLOOR_SOURCE_NONE
    assert assessment.rate_mismatch_ratio == 0.0


def test_rate_mismatch_relative_tolerance_boundary():
    """상대 불일치가 허용오차 이내면 플래그 없음, 넘으면 플래그."""
    derived = 0.90
    inside = derived * (1 + RATE_MISMATCH_RELATIVE_TOLERANCE - 1e-6)
    outside = derived * (1 + RATE_MISMATCH_RELATIVE_TOLERANCE + 1e-6)
    assert FLAG_AMOUNT_RATE_MISMATCH not in _assess(
        reported_rate=inside, effective_rate=inside, amount_derived_rate=derived
    ).flags
    assert FLAG_AMOUNT_RATE_MISMATCH in _assess(
        reported_rate=outside, effective_rate=outside, amount_derived_rate=derived
    ).flags
    # |0.92 - 0.90| / 0.90 == 0.0222 > 0.01 -> 플래그 (여유 있는 케이스)
    over = _assess(reported_rate=0.92, effective_rate=0.92)
    assert FLAG_AMOUNT_RATE_MISMATCH in over.flags
    assert over.rate_mismatch_ratio == pytest.approx(0.022222, abs=1e-5)


def test_rate_mismatch_is_relative_not_absolute():
    """저율 구간에서도 같은 잣대가 적용된다(절대차 기준이면 놓쳤을 케이스)."""
    # 절대차 0.012 는 옛 절대 기준(0.02)을 통과하지만, 0.60 대비 2% 상대 불일치다.
    assessment = _assess(reported_rate=0.612, effective_rate=0.612, amount_derived_rate=0.60)
    assert FLAG_AMOUNT_RATE_MISMATCH in assessment.flags


def test_missing_amount_derived_rate_flag_skips_mismatch():
    assessment = _assess(amount_derived_rate=0.0)
    assert FLAG_MISSING_AMOUNT_DERIVED_RATE in assessment.flags
    assert FLAG_AMOUNT_RATE_MISMATCH not in assessment.flags
    assert assessment.rate_mismatch_ratio is None


def test_missing_reported_rate_skips_floor_and_mismatch_checks():
    """보고 낙찰률이 없으면 기초금액-basis 역산율로 예정가-basis 하한을 재지 않는다."""
    assessment = _assess(reported_rate=None, published_floor_rate=0.95)
    assert FLAG_MISSING_REPORTED_RATE in assessment.flags
    assert FLAG_BELOW_LEGAL_FLOOR not in assessment.flags
    assert FLAG_AMOUNT_RATE_MISMATCH not in assessment.flags
    # 하한 자체는 해석되어 리포트에 남는다(판정만 생략).
    assert assessment.legal_floor_rate == 0.95


def test_below_legal_floor_tolerance_boundary():
    floor = 0.88
    just_inside = floor - LEGAL_FLOOR_UNDERCUT_TOLERANCE + 0.0001
    just_below = floor - LEGAL_FLOOR_UNDERCUT_TOLERANCE - 0.0001
    assert FLAG_BELOW_LEGAL_FLOOR not in _assess(
        reported_rate=just_inside,
        effective_rate=just_inside,
        amount_derived_rate=just_inside,
        published_floor_rate=floor,
    ).flags
    flagged = _assess(
        reported_rate=just_below,
        effective_rate=just_below,
        amount_derived_rate=just_below,
        published_floor_rate=floor,
    )
    assert FLAG_BELOW_LEGAL_FLOOR in flagged.flags
    assert flagged.floor_undercut == pytest.approx(floor - just_below, abs=1e-9)


def test_published_floor_wins_over_construction_era_tier():
    assessment = _assess(
        group="construction",
        category="construction",
        published_floor_rate=0.85,
        estimation_amount=500_000_000.0,
        reference_date=date(2026, 2, 1),
    )
    assert assessment.legal_floor_rate == 0.85
    assert assessment.legal_floor_source == FLOOR_SOURCE_PUBLISHED


def test_construction_era_tier_is_era_correct_not_retroactive():
    """같은 낙찰률 0.88 이 신율 시행 후에는 하회, 시행 전에는 정상이어야 한다(#197/#198)."""
    common = {
        "group": "construction",
        "category": "construction",
        "reported_rate": 0.88,
        "effective_rate": 0.88,
        "amount_derived_rate": 0.88,
        "estimation_amount": 500_000_000.0,  # 5억 -> 10억 미만 구간
    }
    after = _assess(**common, reference_date=date(2026, 2, 1))
    assert after.legal_floor_rate == pytest.approx(0.89745)
    assert after.legal_floor_source == FLOOR_SOURCE_ERA_TIER
    assert FLAG_BELOW_LEGAL_FLOOR in after.flags

    before = _assess(**common, reference_date=date(2025, 12, 1))
    assert before.legal_floor_rate == pytest.approx(0.87745)
    assert FLAG_BELOW_LEGAL_FLOOR not in before.flags


def test_non_construction_category_never_resolves_era_tier():
    assessment = _assess(
        category="service",
        estimation_amount=500_000_000.0,
        reference_date=date(2026, 2, 1),
    )
    assert assessment.legal_floor_rate is None
    assert assessment.legal_floor_source == FLOOR_SOURCE_NONE


def test_basis_contamination_flag_and_clean_basis():
    assert FLAG_BASE_BASIS_CONTAMINATED in _assess(basis=BASIS_DERIVED_YEGA).flags
    assert FLAG_BASE_BASIS_CONTAMINATED not in _assess(basis=BASIS_CLEAN).flags


def test_low_rate_flags_stack_for_construction():
    assessment = _assess(
        group="construction",
        category="construction",
        reported_rate=0.70,
        effective_rate=0.70,
        amount_derived_rate=0.70,
    )
    assert FLAG_LOW_ACTUAL_RATE in assessment.flags
    assert FLAG_CONSTRUCTION_LOW_RATE_REVIEW in assessment.flags


def test_flags_combine_across_axes():
    """분모 불일치 + basis 오염이 동시에 붙는다(첫 매칭 종료가 아님)."""
    assessment = _assess(
        basis=BASIS_DERIVED_YEGA,
        reported_rate=0.95,
        effective_rate=0.95,
        amount_derived_rate=0.90,
    )
    assert FLAG_AMOUNT_RATE_MISMATCH in assessment.flags
    assert FLAG_BASE_BASIS_CONTAMINATED in assessment.flags


def test_as_details_carries_tolerances_for_audit():
    details = _assess(published_floor_rate=0.88).as_details()
    assert details["legal_floor_rate"] == 0.88
    assert details["legal_floor_source"] == FLOOR_SOURCE_PUBLISHED
    assert details["floor_undercut_tolerance"] == LEGAL_FLOOR_UNDERCUT_TOLERANCE
    # rate-basis 게이트의 판단 근거도 행 단위로 추적 가능해야 한다.
    assert details["rate_basis_independence_tolerance"] == RATE_BASIS_INDEPENDENCE_TOLERANCE
    assert details["reserve_price_count"] == 15
    assert details["rate_basis_unverified"] is False


# ── 리포트 섹션 빌더 ──────────────────────────────────────────────────────────
def _fake_aggregate(rows: list[dict]) -> dict:
    """스크립트 ``aggregate`` 자리에 주입되는 최소 가짜 집계기."""
    errors = [row["err"] for row in rows if "err" in row]
    return {
        "target_count": len(rows),
        "recommended": {
            "mean_absolute_amount_error_pct": (
                sum(errors) / len(errors) if errors else None
            )
        },
    }


def _agency_row(agency: str, display: str, err: float) -> dict:
    return {"agency_group": agency, "agency_display": display, "err": err}


def test_build_agency_axis_report_buckets_and_counts():
    rows = [
        _agency_row("a", "울산광역시 울주군", 0.001),
        _agency_row("a", "울산광역시 울주군", 0.002),
        _agency_row("a", "울산광역시 울주군", 0.003),
        _agency_row("b", "부산항만공사", 0.20),
        _agency_row("c", "여수광양항만공사", 0.30),
        _agency_row(AGENCY_UNKNOWN_KEY, AGENCY_UNKNOWN_KEY, 0.05),
    ]
    report = build_agency_axis_report(rows, aggregate_fn=_fake_aggregate, min_samples=3)

    assert list(report["by_agency"]) == ["a", AGENCY_ETC_KEY, AGENCY_UNKNOWN_KEY]
    assert report["by_agency"]["a"]["target_count"] == 3
    assert report["by_agency"][AGENCY_ETC_KEY]["target_count"] == 2

    summary = report["summary"]
    assert summary["min_samples"] == 3
    assert summary["distinct_agency_count"] == 4
    assert summary["reported_agency_count"] == 2  # a + _unknown
    assert summary["etc_agency_count"] == 2
    assert summary["etc_sample_count"] == 2
    assert summary["unknown_sample_count"] == 1
    assert summary["evaluated_sample_count"] == 6
    assert report["agency_displays"]["a"] == "울산광역시 울주군"


def test_build_agency_axis_report_worst_case_ranking_marks_residual():
    rows = [
        _agency_row("a", "울주군", 0.001),
        _agency_row("a", "울주군", 0.001),
        _agency_row("b", "부산항만공사", 0.40),
    ]
    report = build_agency_axis_report(
        rows, aggregate_fn=_fake_aggregate, min_samples=2, worst_limit=2
    )
    worst = report["worst_agencies"]
    assert [item["agency"] for item in worst] == [AGENCY_ETC_KEY, "a"]
    assert worst[0]["is_residual"] is True
    assert worst[0]["mean_absolute_amount_error_pct"] == pytest.approx(0.40)
    assert worst[1]["is_residual"] is False
    assert worst[1]["agency_display"] == "울주군"


def test_build_agency_axis_report_tie_break_is_ascending_key():
    """동점일 때 tie-break 는 주석대로 키 오름차순이다(reverse 로 뒤집히지 않는다)."""
    rows = [
        _agency_row("b", "B기관", 0.10),
        _agency_row("b", "B기관", 0.10),
        _agency_row("a", "A기관", 0.10),
        _agency_row("a", "A기관", 0.10),
    ]
    report = build_agency_axis_report(
        rows, aggregate_fn=_fake_aggregate, min_samples=2, worst_limit=5
    )
    assert [item["agency"] for item in report["worst_agencies"]] == ["a", "b"]


def test_build_agency_axis_report_missing_metric_sorts_last():
    """지표를 못 낸 버킷은 오차가 아무리 큰 버킷보다도 뒤로 간다."""
    rows = [
        {"agency_group": "a", "agency_display": "A기관", "err": 0.50},
        {"agency_group": "a", "agency_display": "A기관", "err": 0.50},
        # err 키가 없어 _fake_aggregate 가 None 을 낸다.
        {"agency_group": "z", "agency_display": "Z기관"},
        {"agency_group": "z", "agency_display": "Z기관"},
    ]
    report = build_agency_axis_report(
        rows, aggregate_fn=_fake_aggregate, min_samples=2, worst_limit=5
    )
    assert [item["agency"] for item in report["worst_agencies"]] == ["a", "z"]


def test_build_agency_axis_report_handles_empty_rows():
    report = build_agency_axis_report([], aggregate_fn=_fake_aggregate)
    assert report["by_agency"] == {}
    assert report["worst_agencies"] == []
    assert report["summary"]["evaluated_sample_count"] == 0


def test_build_quality_flag_report_counts_and_partition():
    rows = [
        {"data_quality_flags": [], "err": 0.001},
        {"data_quality_flags": [FLAG_AMOUNT_RATE_MISMATCH], "err": 0.10},
        {
            "data_quality_flags": [FLAG_AMOUNT_RATE_MISMATCH, FLAG_BELOW_LEGAL_FLOOR],
            "err": 0.20,
        },
    ]
    report = build_quality_flag_report(rows, aggregate_fn=_fake_aggregate)

    assert report["flag_counts"][FLAG_AMOUNT_RATE_MISMATCH] == 2
    assert report["flag_counts"]["clean"] == 1
    assert report["flag_counts"][FLAG_BELOW_LEGAL_FLOOR] == 1

    partition = report["partition"]
    assert partition["all"]["target_count"] == 3
    assert partition["flag_free"]["target_count"] == 1
    assert partition["flagged"]["target_count"] == 2
    # 겹치지 않는 3분할이라 검산이 성립한다.
    assert (
        partition["flag_free"]["target_count"] + partition["flagged"]["target_count"]
        == partition["all"]["target_count"]
    )
    # 플래그 제외 전/후 오차 비교가 실제로 갈린다.
    assert partition["flag_free"]["recommended"]["mean_absolute_amount_error_pct"] == pytest.approx(0.001)
    assert partition["all"]["recommended"]["mean_absolute_amount_error_pct"] > 0.09


def test_quality_flag_report_surfaces_contamination_from_evaluated_scope():
    """집계 스코프가 clean-only 라도 basis 오염 건수가 리포트에서 보여야 한다.

    회귀 가드: 오염 플래그를 집계 스코프에서만 세면 구조적으로 항상 0이 되어
    "오염 0건"으로 오독된다(#199 기준 실제 오염 비율은 ~66%).
    """
    clean_row = {"data_quality_flags": [], "basis": BASIS_CLEAN, "err": 0.001}
    contaminated = [
        {
            "data_quality_flags": [FLAG_BASE_BASIS_CONTAMINATED],
            "basis": BASIS_DERIVED_YEGA,
            "err": 0.30,
        },
        {
            "data_quality_flags": [FLAG_BASE_BASIS_CONTAMINATED],
            "basis": BASIS_DERIVED_YEGA,
            "err": 0.40,
        },
    ]
    report = build_quality_flag_report(
        [clean_row],  # 집계 스코프 = clean 선필터 결과
        aggregate_fn=_fake_aggregate,
        evaluated_rows=[clean_row, *contaminated],
    )

    # 집계 스코프에서는 오염이 0 (설계대로 — 오차 지표를 오염 표본이 흔들지 않는다).
    assert report["flag_counts"].get(FLAG_BASE_BASIS_CONTAMINATED, 0) == 0
    # 전체 평가 행 기준으로는 실제 오염 건수가 드러난다.
    assert report["evaluated_flag_counts"][FLAG_BASE_BASIS_CONTAMINATED] == 2

    scope = report["scope"]
    assert scope["aggregated_count"] == 1
    assert scope["evaluated_count"] == 3
    assert scope["excluded_from_aggregation"] == 2
    # 오차 지표 자체는 여전히 clean 스코프 기준이다.
    assert report["partition"]["all"]["target_count"] == 1


def test_quality_flag_report_defaults_evaluated_scope_to_rows():
    rows = [{"data_quality_flags": [], "err": 0.001}]
    report = build_quality_flag_report(rows, aggregate_fn=_fake_aggregate)
    assert report["scope"]["excluded_from_aggregation"] == 0
    assert report["evaluated_flag_counts"] == report["flag_counts"]


# ── 스크립트 배선(순수 헬퍼) ─────────────────────────────────────────────────
def _target(
    issuing: str | None = None,
    demand: str | None = None,
    group: str = "service",
    stored_agency_name: str | None = None,
):
    moment = datetime(2026, 7, 1, tzinfo=UTC)
    return holdouts.build_target(
        group=group,
        group_source="test",
        result=TenderResult(),
        project=Project(issuing_agency=issuing, demand_agency=demand),
        historical=HistoricalData(agency_name=stored_agency_name),
        event_at=moment,
        available_at=moment,
    )


def test_build_target_derives_agency_axis_fields():
    target = _target(issuing="울산광역시 울주군")
    assert target.agency_group == "울산광역시울주군"
    assert target.agency_display == "울산광역시 울주군"
    assert target.group == "service"  # 예측 입력은 업무구분 그대로


def test_selection_key_switches_axis_without_touching_prediction_group():
    target = _target(issuing="부산항만공사", group="construction")
    assert holdouts.selection_key(target, holdouts.GROUP_BY_BUSINESS_GROUP) == "construction"
    assert holdouts.selection_key(target, holdouts.GROUP_BY_AGENCY) == "부산항만공사"


def test_drop_agency_history_removes_only_the_target_agency():
    history = [
        {"agency_name": "울산광역시 울주군", "bid_rate": 0.88},
        {"agency_name": "(주)해림종합건설", "bid_rate": 0.89},
        {"agency_name": None, "bid_rate": 0.90},
    ]
    kept = holdouts.drop_agency_history(history, {"울산광역시울주군"})
    assert [point["bid_rate"] for point in kept] == [0.89, 0.90]


def test_drop_agency_history_noop_without_agency_key():
    history = [{"agency_name": "울산광역시 울주군"}]
    assert holdouts.drop_agency_history(history, set()) == history
    assert holdouts.drop_agency_history(history, {""}) == history


def test_agency_match_keys_cover_issuing_demand_and_stored_name():
    """발주≠수요 공고는 세 이름이 모두 제외 키에 들어간다."""
    target = _target(
        issuing="조달청",
        demand="울산광역시 울주군",
        stored_agency_name="울주군 건설과",
    )
    assert target.agency_group == "조달청"  # 분할 키는 발주기관 우선(불변)
    assert target.agency_match_keys == {"조달청", "울산광역시울주군", "울주군건설과"}


def test_exclude_agency_history_catches_demand_agency_stored_rows():
    """회귀 가드(누수): 타깃의 과거 낙찰이 수요기관명으로 적재돼 있어도 제외된다.

    `HistoricalData.agency_name` 은 수집 시 `opening_demand_agency or demand_agency
    or issuing_agency` 순으로 적재되는데(persistence), 분할 키는 발주기관 우선이다.
    발주≠수요 공고에서 단일 키로 필터하면 같은 기관 이력이 다른 이름으로 통과해
    "unseen agency" 수치가 낙관적으로 오염된다.
    """
    target = _target(issuing="조달청", demand="울산광역시 울주군")
    history = [
        # 타깃 기관의 과거 낙찰이 수요기관명으로 적재된 행 — 반드시 제외돼야 한다.
        {"agency_name": "울산광역시 울주군", "bid_rate": 0.88},
        # 발주기관명으로 적재된 행도 제외.
        {"agency_name": "조달청", "bid_rate": 0.885},
        # 무관한 기관은 유지.
        {"agency_name": "부산항만공사", "bid_rate": 0.89},
    ]
    kept = holdouts.drop_agency_history(history, target.agency_match_keys)
    assert [point["agency_name"] for point in kept] == ["부산항만공사"]

    # 분할 키(발주기관) 하나만 썼다면 수요기관명 행이 살아남았을 것이다 — 누수 재현.
    leaky = holdouts.drop_agency_history(history, {target.agency_group})
    assert "울산광역시 울주군" in [point["agency_name"] for point in leaky]


def test_exclude_agency_history_catches_opening_demand_agency_stored_rows():
    """opening 수요기관으로 적재된 타깃 자신의 이름도 제외 키에 포함된다."""
    target = _target(
        issuing="조달청",
        demand="울산광역시 울주군",
        stored_agency_name="울주군 상하수도사업소",
    )
    history = [{"agency_name": "울주군 상하수도사업소", "bid_rate": 0.88}]
    assert holdouts.drop_agency_history(history, target.agency_match_keys) == []


def test_default_output_path_is_fixed_for_agency_axis():
    generated_at = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    assert (
        holdouts.default_output_path(holdouts.GROUP_BY_AGENCY, generated_at)
        == holdouts.AGENCY_AXIS_FIXED_OUT
    )
    business = holdouts.default_output_path(holdouts.GROUP_BY_BUSINESS_GROUP, generated_at)
    assert business == "models/reports/latest-award-holdout-20260726-120000.json"


def _seed_award(
    test_db,
    *,
    category: str,
    agency: str,
    announced_at: datetime,
    base_amount: float,
    reserve_prices: list[float] | None = None,
) -> str:
    """Persist one settled notice and return its notice number.

    ``reserve_prices`` 는 복수예비가격 Text(JSON) 컬럼에 그대로 적재된다 — 하한 판정의
    rate-basis 게이트가 그 개수를 보기 때문에 배선 테스트에서 필요하다.
    """
    notice_number = f"SEL-{agency}-{announced_at:%Y%m%d}"
    project = Project(
        title=f"{agency} {category} 공고",
        description="적격심사 가격입찰",
        budget_estimate=base_amount * 0.9,
        category=category,
        status="awarded",
        issuing_agency=agency,
        created_at=announced_at,
    )
    test_db.add(project)
    test_db.flush()
    test_db.add_all(
        [
            HistoricalData(
                project_id=project.id,
                notice_number=notice_number,
                agency_name=agency,
                category=category,
                base_amount=base_amount,
                predicted_price=base_amount * 0.88,
                bid_rate=0.88,
                opened_at=announced_at,
                reserve_prices=(
                    json.dumps(reserve_prices) if reserve_prices is not None else None
                ),
            ),
            TenderResult(
                project_id=project.id,
                winning_amount=base_amount * 0.88,
                winning_rate=0.88,
                result_status="awarded",
                announced_at=announced_at,
                created_at=announced_at,
            ),
        ]
    )
    test_db.flush()
    return notice_number


def _select(test_db, *, group_by: str):
    return holdouts.select_latest_targets(
        test_db,
        service=PredictionDatasetService(),
        groups=("construction", "service"),
        now=datetime(2026, 7, 1, tzinfo=UTC),
        timestamp_grace_hours=9.0,
        candidate_limit=100,
        targets_per_group=1,
        max_targets=0,
        group_by=group_by,
    )


def test_business_group_selection_order_still_follows_groups_argument(test_db):
    """회귀 가드: 기본 축의 출력 순서는 이벤트 시각이 아니라 --groups 순서다.

    service 공고가 더 최신이어도 construction 이 먼저 나와야 한다(기존 동작).
    """
    _seed_award(
        test_db,
        category="construction",
        agency="울산광역시 울주군",
        announced_at=datetime(2026, 5, 1, tzinfo=UTC),
        base_amount=90_000_000.0,
    )
    _seed_award(
        test_db,
        category="service",
        agency="부산항만공사",
        announced_at=datetime(2026, 6, 1, tzinfo=UTC),
        base_amount=120_000_000.0,
    )
    selected = _select(test_db, group_by=holdouts.GROUP_BY_BUSINESS_GROUP)
    assert [target.group for target in selected] == ["construction", "service"]


def test_agency_axis_selection_caps_one_per_agency(test_db):
    """agency 축은 기관마다 targets_per_group 만큼 뽑고 최신 순으로 배열한다."""
    _seed_award(
        test_db,
        category="construction",
        agency="울산광역시 울주군",
        announced_at=datetime(2026, 5, 1, tzinfo=UTC),
        base_amount=90_000_000.0,
    )
    _seed_award(
        test_db,
        category="construction",
        agency="울산광역시 울주군",
        announced_at=datetime(2026, 5, 10, tzinfo=UTC),
        base_amount=95_000_000.0,
    )
    _seed_award(
        test_db,
        category="service",
        agency="부산항만공사",
        announced_at=datetime(2026, 6, 1, tzinfo=UTC),
        base_amount=120_000_000.0,
    )
    selected = _select(test_db, group_by=holdouts.GROUP_BY_AGENCY)
    assert [target.agency_group for target in selected] == ["부산항만공사", "울산광역시울주군"]
    # 예측 입력인 업무구분은 축 전환과 무관하게 그대로 유지된다.
    assert {target.group for target in selected} == {"construction", "service"}


def _evaluate_all(test_db) -> dict[str, dict]:
    """agency 축으로 뽑은 타깃을 전부 평가해 기관 표시명 → 행으로 돌려준다."""
    return {
        row["agency_display"]: row
        for row in (
            holdouts.evaluate_target(
                test_db,
                service=PredictionDatasetService(),
                target=target,
                history_limit=10,
                thresholds=(0.01,),
            )
            for target in _select(test_db, group_by=holdouts.GROUP_BY_AGENCY)
        )
    }


# 기초금액 5억을 ±2% 로 감싸는 복수예비가격 15개(실제 적재 형태와 같은 JSON 리스트).
_RESERVE_PRICES_15 = [
    490_000_000.0 + step * 1_000_000.0 for step in range(15)
]


def test_evaluate_target_feeds_agency_name_into_floor_applicability(test_db, monkeypatch):
    """배선 가드: 하한 적용 범위 판별이 리포트 분할과 같은 기관 원천을 받는다.

    같은 낙찰률(0.88 < era-tier 0.89745)이라도 산학협력단은 하한 모델 적용 대상이
    아니고(``not_applicable``), 산림청 계열은 별도 행정규칙 체계라(``separate_regime``)
    판정이 생략된다. 예비가로 예정가를 독립 재구성할 수 있는 국가기관 공고는 기존대로
    플래그가 붙는다.
    """
    monkeypatch.setattr(
        holdouts,
        "predict_price",
        lambda **kwargs: {"predicted_price": 400_000_000.0, "predicted_bid_rate": 0.88},
    )
    for agency, announced_day in (("인제대학교 산학협력단", 1), ("산림청 홍천국유림관리소", 2)):
        _seed_award(
            test_db,
            category="construction",
            agency=agency,
            announced_at=datetime(2026, 5, announced_day, tzinfo=UTC),
            base_amount=500_000_000.0,
        )
    _seed_award(
        test_db,
        category="construction",
        agency="울산광역시 울주군",
        announced_at=datetime(2026, 5, 3, tzinfo=UTC),
        base_amount=500_000_000.0,
        reserve_prices=_RESERVE_PRICES_15,
    )

    rows = _evaluate_all(test_db)
    verdicts = {
        display: (
            row["data_quality_details"]["floor_applicability"],
            FLAG_BELOW_LEGAL_FLOOR in row["data_quality_flags"],
        )
        for display, row in rows.items()
    }
    assert verdicts["인제대학교 산학협력단"] == ("not_applicable", False)
    assert verdicts["산림청 홍천국유림관리소"] == ("separate_regime", False)
    assert verdicts["울산광역시 울주군"] == ("applicable", True)


def test_evaluate_target_feeds_reserve_price_count_into_rate_basis_gate(
    test_db, monkeypatch
):
    """배선 가드: 복수예비가격 개수가 HistoricalData 에서 품질 판정기까지 흐른다.

    두 공고의 보고 낙찰률은 모두 ``winning_amount / base_amount`` 와 정확히 같다.
    예비가가 없는 쪽은 그 값이 독립 예정가-basis 실측이라는 근거가 없어 하한 판정이
    생략되고(라이브 A군 13건), 예비가 15개가 있는 쪽은 기존대로 판정된다.
    """
    monkeypatch.setattr(
        holdouts,
        "predict_price",
        lambda **kwargs: {"predicted_price": 400_000_000.0, "predicted_bid_rate": 0.88},
    )
    _seed_award(
        test_db,
        category="construction",
        agency="울산광역시 울주군",
        announced_at=datetime(2026, 5, 1, tzinfo=UTC),
        base_amount=500_000_000.0,
    )
    _seed_award(
        test_db,
        category="construction",
        agency="부산광역시 기장군",
        announced_at=datetime(2026, 5, 2, tzinfo=UTC),
        base_amount=500_000_000.0,
        reserve_prices=_RESERVE_PRICES_15,
    )

    rows = _evaluate_all(test_db)
    without_reserves = rows["울산광역시 울주군"]["data_quality_details"]
    with_reserves = rows["부산광역시 기장군"]["data_quality_details"]

    assert without_reserves["reserve_price_count"] == 0
    assert without_reserves["rate_basis_unverified"] is True
    assert FLAG_BELOW_LEGAL_FLOOR not in rows["울산광역시 울주군"]["data_quality_flags"]

    assert with_reserves["reserve_price_count"] == len(_RESERVE_PRICES_15)
    assert with_reserves["rate_basis_unverified"] is False
    assert FLAG_BELOW_LEGAL_FLOOR in rows["부산광역시 기장군"]["data_quality_flags"]


def test_group_by_agency_selection_caps_per_agency():
    """agency 축에서는 기관마다 targets_per_group 만큼만 남는다(업종 축과 무관)."""
    parser = holdouts.build_parser()
    args = parser.parse_args(["--group-by", "agency", "--min-agency-samples", "2"])
    assert args.group_by == holdouts.GROUP_BY_AGENCY
    assert args.min_agency_samples == 2
    assert args.exclude_agency_history is False
    # 기본값은 기존 동작(업종 축) 유지.
    assert holdouts.build_parser().parse_args([]).group_by == holdouts.GROUP_BY_BUSINESS_GROUP
