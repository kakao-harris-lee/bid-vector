"""Tests for license_eligibility + report_license_eligibility.

면허 매칭 규칙의 값 테이블(§4.5.3)과 리포트 집계를 실 API/DB 접속 없이 검증한다.
공고 원문 샘플은 라이브 ``eligibility_raw["license_limits"]`` 실측 형태를 쓴다.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.single_user import split_multi_value_text
from app.core.time import utc_now
from app.services.license_eligibility import (
    ELIGIBLE_PRECISION_CAVEAT,
    GROUP_SEMANTICS_ASSUMPTION,
    UNGROUPED_KEY,
    VERDICT_ELIGIBLE,
    VERDICT_INELIGIBLE,
    VERDICT_UNKNOWN,
    assess_license_eligibility,
    license_limit_names,
    normalize_license_key,
    parse_license_limit_groups,
    profile_license_keys,
)

# Load the report script by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "report_license_eligibility",
    Path(__file__).resolve().parents[1] / "scripts" / "report_license_eligibility.py",
)
report = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = report
_SPEC.loader.exec_module(report)


# canonical operator 프로필 실값(해양 세그먼트, 한글 별칭 표기).
MARINE_PROFILE = "엔지니어링, 항만및해안, 해양엔지니어링, 수로조사"

# 라이브 실측 샘플: 그룹1 = 중간처리업 단독 / 그룹2 = 중간처리업 + 수집·운반업.
LIVE_WASTE_LIMITS = {
    "license_limits": [
        {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "1", "lmtSno": "1"},
        {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "2", "lmtSno": "2"},
        {
            "lcnsLmtNm": "건설폐기물 수집·운반업/6728",
            "permsnIndstrytyList": "[건설폐기물 중간처리업/1253]",
            "lmtGrpNo": "2",
            "lmtSno": "3",
        },
    ]
}


def _row(
    eligibility_raw: dict | None, notice_number: str = "R0001", title: str = "공고"
) -> Any:
    """Build a report ReportRow."""
    return report.ReportRow(
        notice_number=notice_number, title=title, eligibility_raw=eligibility_raw
    )


# --- 그룹 파싱 ----------------------------------------------------------------


def test_parse_single_group():
    groups = parse_license_limit_groups(
        {"license_limits": [{"lcnsLmtNm": "전기공사업/0037", "lmtGrpNo": "1"}]}
    )

    assert [g.group_no for g in groups] == ["1"]
    assert groups[0].names() == ("전기공사업",)


def test_parse_multiple_groups_preserves_order_and_membership():
    groups = parse_license_limit_groups(LIVE_WASTE_LIMITS)

    assert [g.group_no for g in groups] == ["1", "2"]
    # 그룹 내 = AND 이므로 그룹2 는 두 면허를 모두 요구한다.
    assert groups[0].names() == ("건설폐기물 중간처리업",)
    assert groups[1].names() == ("건설폐기물 중간처리업", "건설폐기물 수집·운반업")


def test_parse_missing_group_no_collapses_to_single_group():
    # lmtGrpNo 결측 행은 하나의 그룹(AND)으로 묶인다 — OR 로 흩는 것보다 보수적.
    groups = parse_license_limit_groups(
        {
            "license_limits": [
                {"lcnsLmtNm": "전기공사업/0037"},
                {"lcnsLmtNm": "정보통신공사업/0042", "lmtGrpNo": ""},
            ]
        }
    )

    assert [g.group_no for g in groups] == [UNGROUPED_KEY]
    assert groups[0].names() == ("전기공사업", "정보통신공사업")


def test_parse_ignores_malformed_rows():
    groups = parse_license_limit_groups(
        {"license_limits": ["문자열행", {"lcnsLmtNm": "  "}, {"lmtGrpNo": "1"}]}
    )

    assert groups == []


def test_required_keys_subset_equals_missing_empty():
    """required_keys() ⊆ profile_keys 가 group.missing() 이 빈 것과 동치다(커널 projection 계약).

    그룹-OR 커널은 그룹내 AND 를 "합집합 요구 키 ⊆ 보유 키" 부분집합으로 판정한다.
    이는 요구별 is_held_by 를 모두 AND 한 것(= missing 이 빔)과 같아야 커널 위임이
    면허 게이트 판정을 바꾸지 않는다(합집합 collapse 안전성 회귀 가드).
    """
    groups = parse_license_limit_groups(LIVE_WASTE_LIMITS)
    profile_keys = profile_license_keys(MARINE_PROFILE)
    for group in groups:
        assert (group.required_keys() <= profile_keys) == (
            not group.missing(profile_keys)
        )

    # 그룹1(중간처리업)을 실제 보유하면 부분집합 성립 = missing 빔.
    holds = profile_license_keys("건설폐기물 중간처리업")
    assert groups[0].required_keys() <= holds
    assert groups[0].missing(holds) == ()
    # 미보유면 부분집합 불성립 = missing 존재.
    assert not (groups[0].required_keys() <= profile_license_keys(""))


# --- verdict 값 테이블 --------------------------------------------------------


def test_verdict_eligible_when_a_group_is_fully_held():
    # 별칭 등재 면허: "해양조사정보업(수로측량업)" ↔ 프로필 "수로조사" (HYDRO001).
    result = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": "해양조사정보업(수로측량업)/5034", "lmtGrpNo": "1"}]},
        MARINE_PROFILE,
    )

    assert result.verdict == VERDICT_ELIGIBLE
    assert result.matched_groups == ("1",)
    assert result.missing_by_group == {}
    assert result.evidence and "그룹 1 충족" in result.evidence[0]


def test_verdict_eligible_needs_only_one_alternative_group():
    # 그룹 간 = OR: 그룹1(보유) 충족이면 그룹2(미보유) 미충족이어도 eligible.
    result = assess_license_eligibility(
        {
            "license_limits": [
                {"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"},
                {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "2"},
            ]
        },
        MARINE_PROFILE,
    )

    assert result.verdict == VERDICT_ELIGIBLE
    assert result.matched_groups == ("1",)
    assert result.missing_by_group == {"2": ("건설폐기물 중간처리업",)}


def test_verdict_ineligible_when_group_only_partially_held():
    # 그룹 내 = AND: 그룹의 면허 중 하나만 보유하면 충족이 아니다.
    result = assess_license_eligibility(
        {
            "license_limits": [
                {"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"},
                {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "1"},
            ]
        },
        MARINE_PROFILE,
    )

    assert result.verdict == VERDICT_INELIGIBLE
    assert result.matched_groups == ()
    assert result.missing_by_group == {"1": ("건설폐기물 중간처리업",)}


def test_verdict_ineligible_when_no_group_is_satisfied():
    result = assess_license_eligibility(LIVE_WASTE_LIMITS, MARINE_PROFILE)

    assert result.verdict == VERDICT_INELIGIBLE
    assert result.required_any == ("건설폐기물 중간처리업", "건설폐기물 수집·운반업")


def test_verdict_unknown_when_flags_only():
    # 자격 데이터는 있으나 면허요건(license_limits)이 없으면 판정 불가.
    result = assess_license_eligibility(
        {"flags": {"prtcptPsblRgnNm": "전국"}}, MARINE_PROFILE
    )

    assert result.verdict == VERDICT_UNKNOWN
    assert result.required_any == ()


def test_verdict_unknown_when_eligibility_raw_is_none():
    assert assess_license_eligibility(None, MARINE_PROFILE).verdict == VERDICT_UNKNOWN


def test_verdict_unknown_when_profile_has_no_licenses():
    # 보유 면허 미기재는 "미보유"가 아니라 데이터 공백 — ineligible 로 만들지 않는다.
    result = assess_license_eligibility(LIVE_WASTE_LIMITS, "")

    assert result.verdict == VERDICT_UNKNOWN
    # 요구 면허는 그대로 노출해 무엇 때문에 판정 불가인지 남긴다.
    assert result.required_any == ("건설폐기물 중간처리업", "건설폐기물 수집·운반업")


def test_verdict_unknown_when_rows_are_unparsable():
    result = assess_license_eligibility(
        {"license_limits": [{"lmtGrpNo": "1", "lmtSno": "1"}]}, MARINE_PROFILE
    )

    assert result.verdict == VERDICT_UNKNOWN


# --- 오판 방지 회귀: 별칭 미등재 면허 -----------------------------------------


def test_unregistered_license_is_kept_as_raw_key_not_dropped():
    """별칭 미등재 면허를 버리면 ineligible 이 eligible 로 뒤집힌다(핵심 회귀).

    "건설폐기물 중간처리업" 은 taxonomy LICENSE_ALIASES 에 없다. 정규화 실패로
    버렸다면 그룹의 요구 면허가 0개가 되어 "요건 없음 = 충족"으로 통과해버린다.
    원문 정규화 키로 보존해 ineligible 을 유지해야 한다.
    """
    groups = parse_license_limit_groups(LIVE_WASTE_LIMITS)
    requirement = groups[0].requirements[0]

    assert requirement.alias_mapped is False  # 별칭 미등재 확인
    assert requirement.keys == frozenset({"건설폐기물중간처리업"})
    assert (
        assess_license_eligibility(LIVE_WASTE_LIMITS, MARINE_PROFILE).verdict
        == VERDICT_INELIGIBLE
    )


def test_unregistered_license_matches_when_profile_literally_holds_it():
    # 원문 키 비교는 문자열 동치 — 프로필이 같은 면허를 기재하면 매칭된다.
    result = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "1"}]},
        "건설폐기물 중간처리업, 토목공사업",
    )

    assert result.verdict == VERDICT_ELIGIBLE


def test_generic_eng001_alias_no_longer_overmatches():
    """정밀화 회귀 가드(의도된 변경 — 과거 특성화값 eligible → ineligible).

    ENG001 별칭에는 bare "감리" 가 들어있어(taxonomy.py) 과거엔 공고 "정보시스템
    감리법인" 이 프로필의 "엔지니어링" 과 ENG001 으로 만나 eligible 이 됐다(2026-07-20
    라이브 오탐, 해양 엔지니어링과 무관한 AI 플랫폼 감리 용역). 이제 ENG001 은
    :data:`IMPRECISE_MATCH_CODES` 로 면허 대조 키에서 배제되므로, 이 이름은 원문
    정규화 키("정보시스템감리법인")로 비교돼 프로필과 매칭되지 않는다.
    """
    result = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": "정보시스템 감리법인", "lmtGrpNo": "1"}]},
        MARINE_PROFILE,
    )

    assert result.verdict == VERDICT_INELIGIBLE  # ENG001 배제 — 오탐 제거


# --- 파싱 불가 행 노출 --------------------------------------------------------


def test_unparsable_rows_are_counted_when_all_rows_fail():
    result = assess_license_eligibility(
        {"license_limits": [{"lmtGrpNo": "1"}, {"lmtGrpNo": "2"}]}, MARINE_PROFILE
    )

    assert result.verdict == VERDICT_UNKNOWN
    assert result.unparsable_rows == 2
    assert result.has_unparsable_rows is True
    # 행이 아예 없는 경우와 구분된다.
    assert assess_license_eligibility(None, MARINE_PROFILE).unparsable_rows == 0


def test_partially_unparsable_rows_are_surfaced_in_verdict():
    """일부 행만 못 읽으면 판정은 **줄어든 요건**으로 내려진다 — 그 사실을 남긴다.

    조용히 버리면 요건이 줄어 eligible 쪽으로 관대해지는데 결과만 봐서는 알 수
    없다. 판정은 그대로 내리되 건수와 evidence 로 신뢰도 저하를 노출한다.
    """
    result = assess_license_eligibility(
        {
            "license_limits": [
                {"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"},
                {"lmtGrpNo": "1"},  # 면허명 없음 — 요건에서 빠진다
            ]
        },
        MARINE_PROFILE,
    )

    assert result.verdict == VERDICT_ELIGIBLE
    assert result.unparsable_rows == 1
    assert any("읽지 못한 행 1건" in line for line in result.evidence)


def test_aggregate_splits_unparsable_from_missing_data():
    rows = [
        _row({"license_limits": [{"lmtGrpNo": "1"}]}, notice_number="R0001"),  # 파싱 불가
        _row(None, notice_number="R0002"),  # 행 자체 없음
        _row(
            {
                "license_limits": [
                    {"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"},
                    {"lmtGrpNo": "1"},
                ]
            },
            notice_number="R0003",
        ),  # 일부만 파싱 불가 — 판정은 내려짐
    ]

    summary = report.aggregate_eligibility(rows, MARINE_PROFILE)

    # 파싱 불가와 데이터 부재를 합치면 "자격 데이터가 아예 없다"는 오해가 생긴다.
    assert summary.unparsable_only == 1
    assert summary.without_eligibility == 1
    assert summary.with_eligibility == 1
    assert summary.partial_unparsable == 1


def test_normalize_license_key_strips_code_suffix_and_separators():
    assert normalize_license_key("건설폐기물 수집·운반업/6728") == "건설폐기물수집운반업"
    assert normalize_license_key("해양조사정보업(수로측량업)/5034") == "해양조사정보업수로측량업"


def test_profile_license_keys_holds_both_codes_and_raw_names():
    keys = profile_license_keys(MARINE_PROFILE)

    # taxonomy canonical 코드 — ENG001 은 IMPRECISE_MATCH_CODES 로 대조 키에서 배제된다.
    assert {"PORT001", "MAR001", "HYDRO001"} <= keys
    assert "ENG001" not in keys
    # 항목별 원문 정규화 키(별칭 미등재/포괄배제 면허 비교용)
    assert "항만및해안" in keys
    assert profile_license_keys(None) == frozenset()


# --- ENG001 포괄 별칭 정밀화: 실보유 5면허 대조 (2026-07-21 확정) -------------

# 운영자 실보유 면허 5종(2026-07-21)의 license_codes 저장 표현.
# "엔지니어링사업(항만, 해안)" 은 내부 쉼표가 있어 split_multi_value_text 가 토큰을
# 깨뜨리므로 저장 시 내부 쉼표를 가운뎃점(·)으로 바꾼다. normalize_license_key 가
# 쉼표·공백·가운뎃점을 모두 제거하므로 공고 원문("항만, 해안")과 동일 키로 매칭된다.
OPERATOR_LICENSES_2026_07_21 = (
    "엔지니어링사업(해양), 엔지니어링사업(항만·해안), " "해양조사정보업(수로측량업), 해양조사정보업(해도제작업), 해양조사정보업(해양관측업)"
)


def test_operator_license_storage_splits_into_five_tokens():
    """내부 쉼표(항만·해안)를 가운뎃점으로 둔 저장 표현이 정확히 5토큰으로 쪼개진다."""
    assert split_multi_value_text(OPERATOR_LICENSES_2026_07_21) == [
        "엔지니어링사업(해양)",
        "엔지니어링사업(항만·해안)",
        "해양조사정보업(수로측량업)",
        "해양조사정보업(해도제작업)",
        "해양조사정보업(해양관측업)",
    ]


def test_stored_port_haean_token_normalizes_to_notice_comma_form():
    """저장형 '항만·해안'(가운뎃점)이 공고형 '항만, 해안'(쉼표)과 같은 키로 정규화된다."""
    assert normalize_license_key("엔지니어링사업(항만·해안)") == normalize_license_key(
        "엔지니어링사업(항만, 해안)/1234"
    )


@pytest.mark.parametrize(
    "notice_license, expected_verdict",
    [
        # 실보유 종목과 같은 전문분야 → eligible.
        ("엔지니어링사업(해양)/5678", VERDICT_ELIGIBLE),
        ("엔지니어링사업(항만, 해안)/1234", VERDICT_ELIGIBLE),
        ("해양조사정보업(수로측량업)/5034", VERDICT_ELIGIBLE),
        ("해양조사정보업(해도제작업)/5035", VERDICT_ELIGIBLE),
        # 무관 전문분야 → ineligible (과거 ENG001 collapse 로 오탐이던 것들).
        ("엔지니어링사업(전기설비)/9001", VERDICT_INELIGIBLE),
        ("정보시스템 감리법인/6146", VERDICT_INELIGIBLE),
        ("건설엔지니어링업(종합)/4966", VERDICT_INELIGIBLE),
    ],
)
def test_operator_five_licenses_match_only_their_specialty(
    notice_license, expected_verdict
):
    """실보유 5면허가 같은 전문분야 공고만 매칭하고 무관 전문분야는 배제한다.

    ENG001 배제 전에는 "정보시스템 감리법인"·"건설엔지니어링업(종합)" 이 프로필
    엔지니어링과 ENG001 로 collapse 돼 eligible 이었다. 정밀화 후엔 원문 정규화
    키/구체 코드로 비교돼 구분된다. "전기설비" 는 "전기"→ELE001 을 함께 요구하는데
    프로필이 ELE001 을 보유하지 않아 배제된다.
    """
    result = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": notice_license, "lmtGrpNo": "1"}]},
        OPERATOR_LICENSES_2026_07_21,
    )

    assert result.verdict == expected_verdict


def test_distinct_engineering_specialties_do_not_cross_match():
    """전문분야가 다른 엔지니어링 면허끼리 매칭되지 않는다(핵심 collapse 오탐 수정).

    ENG001 배제 전에는 "엔지니어링사업(해양)"·"엔지니어링사업(항만·해안)" 이 모두
    ENG001 하나로 뭉쳐, 해양만 보유해도 항만 요구 공고에 eligible 로 뜨는 오탐이
    있었다. 정밀화 후엔 원문 정규화 키로 비교돼 서로 다른 종목으로 구분된다.
    """
    only_marine = "엔지니어링사업(해양)"

    cross = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": "엔지니어링사업(항만, 해안)/1234", "lmtGrpNo": "1"}]},
        only_marine,
    )
    assert cross.verdict == VERDICT_INELIGIBLE

    # 자기 종목 공고에는 여전히 eligible.
    same = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": "엔지니어링사업(해양)/5678", "lmtGrpNo": "1"}]},
        only_marine,
    )
    assert same.verdict == VERDICT_ELIGIBLE


# --- 리포트 집계 (fake rows, DB 접속 없음) ------------------------------------


def test_aggregate_counts_coverage_and_verdicts():
    rows = [
        _row(LIVE_WASTE_LIMITS, notice_number="R0001"),
        _row(
            {"license_limits": [{"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"}]},
            notice_number="R0002",
        ),
        _row(None, notice_number="R0003"),
        _row({"flags": {"prtcptPsblRgnNm": "전국"}}, notice_number="R0004"),
    ]

    summary = report.aggregate_eligibility(rows, MARINE_PROFILE)

    assert summary.total == 4
    assert summary.with_eligibility == 2
    assert summary.without_eligibility == 2
    assert summary.verdict_counts[VERDICT_ELIGIBLE] == 1
    assert summary.verdict_counts[VERDICT_INELIGIBLE] == 1
    assert summary.verdict_counts[VERDICT_UNKNOWN] == 2


def test_aggregate_collects_eligible_samples_and_required_frequency():
    rows = [
        _row(LIVE_WASTE_LIMITS, notice_number="R0001"),
        _row(LIVE_WASTE_LIMITS, notice_number="R0002"),
        _row(
            {"license_limits": [{"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"}]},
            notice_number="R0003",
            title="항만 해양엔지니어링 용역",
        ),
    ]

    summary = report.aggregate_eligibility(rows, MARINE_PROFILE)

    assert [s.notice_number for s in summary.eligible_samples] == ["R0003"]
    assert summary.eligible_samples[0].matched_licenses == ("해양엔지니어링",)
    # ineligible 공고가 요구한 면허 빈도 = 무엇 때문에 걸러지나.
    assert summary.ineligible_required["건설폐기물 중간처리업"] == 2
    assert summary.ineligible_required["건설폐기물 수집·운반업"] == 2


def test_aggregate_respects_sample_limit():
    eligible_raw = {"license_limits": [{"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"}]}
    rows = [_row(eligible_raw, notice_number=f"R{i:04d}") for i in range(5)]

    summary = report.aggregate_eligibility(rows, MARINE_PROFILE, samples=2)

    assert len(summary.eligible_samples) == 2
    assert summary.verdict_counts[VERDICT_ELIGIBLE] == 5


def test_render_summary_is_kst_and_states_group_assumption():
    summary = report.aggregate_eligibility([_row(LIVE_WASTE_LIMITS)], MARINE_PROFILE)
    text = report.render_summary(
        summary, profile_license_codes=MARINE_PROFILE, top_required=5
    )

    assert "KST" in text
    assert "ineligible=1" in text
    assert "건설폐기물 중간처리업: 1" in text
    # 그룹 의미론 가정과 unknown≠ineligible 고지가 리포트에 함께 나온다.
    assert GROUP_SEMANTICS_ASSUMPTION in text
    assert "ineligible(부적격)이 아니다" in text


def test_render_summary_warns_eligible_precision_is_unverified():
    """stdout 만 보는 사람이 eligible 을 검증된 자격 신호로 오독하면 안 된다.

    이 리포트가 wiring 결정 산출물이므로 정밀도 한계는 코드 주석이 아니라
    출력에 실려야 한다(§2 정직). eligible 이 0건이어도 고지는 유지된다.
    """
    eligible_raw = {"license_limits": [{"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"}]}
    text = report.render_summary(
        report.aggregate_eligibility([_row(eligible_raw)], MARINE_PROFILE),
        profile_license_codes=MARINE_PROFILE,
        top_required=5,
    )

    assert ELIGIBLE_PRECISION_CAVEAT in text
    # 문구는 실패를 과장하지도 축소하지도 않는다 — 미검증이지 "전부 오탐"이 아니다.
    assert "미검증" in ELIGIBLE_PRECISION_CAVEAT
    assert "도메인 확인 필요" in ELIGIBLE_PRECISION_CAVEAT
    # eligible 샘플 헤더 자체에도 주의가 붙어 목록만 보고 넘어가지 않게 한다.
    assert "정밀도 미검증" in text.split("eligible 샘플")[1].split("\n")[0]


# --- load_rows: 열린 공고 필터 (test_db SQLite 픽스처) ------------------------


def test_load_rows_filters_open_and_undeadlined(test_db):
    from app.models.models import Project

    now = utc_now()
    test_db.add_all(
        [
            Project(
                id=1,
                notice_number="R0001",
                title="열린 공고",
                status="open",
                deadline=now + timedelta(days=3),
                eligibility_raw=LIVE_WASTE_LIMITS,
            ),
            Project(
                id=2,
                notice_number="R0002",
                title="마감된 공고",
                status="open",
                deadline=now - timedelta(days=1),
            ),
            Project(
                id=3,
                notice_number="R0003",
                title="낙찰된 공고",
                status="awarded",
                deadline=now + timedelta(days=3),
            ),
        ]
    )
    test_db.commit()

    rows = report.load_rows(test_db)

    assert [r.notice_number for r in rows] == ["R0001"]
    assert rows[0].eligibility_raw == LIVE_WASTE_LIMITS


# --- license_limit_names (표시명 평탄화, 온보딩 후보용) -----------------------


def test_license_limit_names_strips_code_suffix_and_dedupes():
    """면허명을 코드 접미 제거·등장순·중복 제거로 평탄화한다(그룹 의미론 무관)."""
    names = license_limit_names(LIVE_WASTE_LIMITS)

    # "/1253"·"/6728" 접미가 떨어지고, 두 그룹에 중복 등장한 중간처리업은 1회만.
    assert names == ("건설폐기물 중간처리업", "건설폐기물 수집·운반업")


def test_license_limit_names_preserve_precision_no_eng001_collapse():
    """실 corpus 형식 면허명이 전문분야를 구분해 남고 ENG001 로 붕괴하지 않는다.

    canonical 코드로 뽑으면 ENG001 포괄 별칭이 해양/항만/전기설비를 한 코드로
    collapse 시키고 HYDRO001 이 해양조사 하위 종목을 collapse 하지만, 표시명은
    이 정밀 신호를 보존한다(온보딩 license_codes 후보 네임스페이스 근거).
    """
    eligibility_raw = {
        "license_limits": [
            {"lcnsLmtNm": "엔지니어링사업(해양)", "lmtGrpNo": "1"},
            {"lcnsLmtNm": "엔지니어링사업(항만, 해안)", "lmtGrpNo": "2"},
            {"lcnsLmtNm": "엔지니어링사업(전기설비)", "lmtGrpNo": "3"},
            {"lcnsLmtNm": "해양조사정보업(수로측량업)/5034", "lmtGrpNo": "4"},
        ]
    }
    names = license_limit_names(eligibility_raw)

    assert names == (
        "엔지니어링사업(해양)",
        "엔지니어링사업(항만, 해안)",
        "엔지니어링사업(전기설비)",
        "해양조사정보업(수로측량업)",
    )
    assert "ENG001" not in names


def test_license_limit_names_empty_when_no_limits():
    """면허요건이 없으면 빈 튜플(비-dict·결측 포함)."""
    assert license_limit_names(None) == ()
    assert license_limit_names({}) == ()
    assert license_limit_names({"flags": {"association": "Y"}}) == ()
