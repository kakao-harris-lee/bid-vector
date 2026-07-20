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

from app.core.time import utc_now
from app.services.license_eligibility import (
    ELIGIBLE_PRECISION_CAVEAT,
    GROUP_SEMANTICS_ASSUMPTION,
    UNGROUPED_KEY,
    VERDICT_ELIGIBLE,
    VERDICT_INELIGIBLE,
    VERDICT_UNKNOWN,
    assess_license_eligibility,
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


def test_generic_alias_overmatch_is_a_known_limitation():
    """알려진 한계(특성화): taxonomy 의 포괄 별칭이 무관한 면허를 매칭시킨다.

    ENG001 별칭에는 bare "엔지니어링"·"감리" 가 들어있어(taxonomy.py:51) 공고의
    "정보시스템 감리법인" 이 프로필의 "엔지니어링" 과 ENG001 으로 만나 eligible 이
    된다. 2026-07-20 라이브 eligible 3건 중 **이 1건은 명백한 오탐**이고(해양
    엔지니어링과 무관한 AI 플랫폼 감리 용역), 나머지 2건은 실제 엔지니어링
    면허라 도메인 확인이 필요하다 — 즉 정밀도는 **미검증**이지 0 이 아니다.
    이 축을 추천에 연결(D2)하기 전에 별칭 정밀화가 선행돼야 한다. 현재 동작을
    고정해 이후 개선이 회귀가 아니라 의도된 변경으로 드러나게 한다.
    """
    result = assess_license_eligibility(
        {"license_limits": [{"lcnsLmtNm": "정보시스템 감리법인", "lmtGrpNo": "1"}]},
        MARINE_PROFILE,
    )

    assert result.verdict == VERDICT_ELIGIBLE  # 과매칭 — 정밀도 한계


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

    # taxonomy canonical 코드
    assert {"ENG001", "PORT001", "MAR001", "HYDRO001"} <= keys
    # 항목별 원문 정규화 키(별칭 미등재 면허 비교용)
    assert "항만및해안" in keys
    assert profile_license_keys(None) == frozenset()


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
