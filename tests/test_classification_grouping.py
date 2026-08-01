"""``classification/_grouping`` 해석기 특성화 테스트.

협회 가입·기술부문 두 축이 공유하는 그룹핑 해석기를 (a) 매처를 모르는 순수 해석기
표, (b) 두 축 호출부 경로의 실측 형태 입력으로 고정한다. 통합 전 두 복사본
(``_memberships_by_group`` · ``_tech_fields_by_group``)의 출력을 그대로 계약으로
잠근다 — 이 파일이 깨지면 동작이 바뀐 것이다. 실 API/DB 접속 없음.
"""
from __future__ import annotations

import pytest

from app.services.classification._grouping import terms_by_license_group
from app.services.classification.association import _memberships_by_group
from app.services.classification.tech_field import _tech_fields_by_group

# --- 라이브 실측 형태 자격 원문 ------------------------------------------------

ASSOC_RAW = {"license_limits": [{"lcnsLmtNm": "한국엔지니어링협회 가입 업체", "lmtGrpNo": "1"}]}
MARINE_RAW = {"license_limits": [{"lcnsLmtNm": "엔지니어링사업(해양)", "lmtGrpNo": "1"}]}
# 그룹1 = 협회 요건, 그룹2 = 해양 기술부문. 한 raw 에서 두 축이 서로 다른 그룹을
# 제약으로 본다(해석기가 도메인을 모른다는 증거).
MIXED_TWO_GROUP_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "한국엔지니어링협회 가입 업체", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "엔지니어링사업(해양)/3599", "lmtGrpNo": "2"},
    ]
}
# 단일 그룹 안의 두 행 = AND (한 frozenset 으로 union).
SINGLE_GROUP_AND_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "엔지니어링사업(해양)/3599", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "엔지니어링사업(항만, 해안)/3579", "lmtGrpNo": "1"},
    ]
}
# 어느 축도 매핑하지 않는 면허 = 무제약 그룹(빈 frozenset 으로 **남아야** 한다).
LICENSE_ONLY_RAW = {"license_limits": [{"lcnsLmtNm": "토목공사업/1001", "lmtGrpNo": "1"}]}


def _fake_matcher(text: str) -> frozenset[str]:
    """도메인과 무관한 매처 — 해석기가 값 공간을 매처에 위임함을 보인다."""
    return frozenset({text.strip().lower()}) if text and text.strip() else frozenset()


# --- 해석기 값 표 (매처 주입) --------------------------------------------------


@pytest.mark.parametrize(
    ("eligibility_raw", "expected"),
    [
        (None, []),
        ({}, []),
        ({"license_limits": []}, []),
        ({"flags": {"note": "엔지니어링협회"}}, []),
        ({"license_limits": [{"lcnsLmtNm": "가나", "lmtGrpNo": "1"}]}, [{"가나"}]),
        (
            {
                "license_limits": [
                    {"lcnsLmtNm": "가나", "lmtGrpNo": "1"},
                    {"lcnsLmtNm": "다라", "lmtGrpNo": "2"},
                ]
            },
            [{"가나"}, {"다라"}],
        ),
        (
            {
                "license_limits": [
                    {"lcnsLmtNm": "가나", "lmtGrpNo": "1"},
                    {"lcnsLmtNm": "다라", "lmtGrpNo": "1"},
                ]
            },
            [{"가나", "다라"}],
        ),
    ],
)
def test_interpreter_maps_each_group_with_injected_matcher(eligibility_raw, expected):
    """그룹 경계·등장 순서를 유지하며 그룹별로 매처 결과를 union 한다."""
    assert terms_by_license_group(eligibility_raw, _fake_matcher) == [
        frozenset(group) for group in expected
    ]


def test_interpreter_returns_frozensets():
    """반환 원소는 frozenset 이다(호출부 group_or 커널의 입력 계약)."""
    groups = terms_by_license_group(ASSOC_RAW, _fake_matcher)
    assert all(isinstance(group, frozenset) for group in groups)


# --- 두 축 호출부 경로 (통합 전 계약 고정) -------------------------------------


@pytest.mark.parametrize(
    ("eligibility_raw", "memberships", "tech_fields"),
    [
        (None, [], []),
        ({}, [], []),
        (ASSOC_RAW, [{"엔지니어링협회"}], [set()]),
        (MARINE_RAW, [set()], [{"해양엔지니어링"}]),
        (MIXED_TWO_GROUP_RAW, [{"엔지니어링협회"}, set()], [set(), {"해양엔지니어링"}]),
        (SINGLE_GROUP_AND_RAW, [set()], [{"해양엔지니어링", "항만및해안"}]),
        (LICENSE_ONLY_RAW, [set()], [set()]),
    ],
)
def test_both_axes_keep_their_pre_consolidation_output(
    eligibility_raw, memberships, tech_fields
):
    """두 축 래퍼는 각자의 어휘만 보고, 서로의 요건을 흡수하지 않는다."""
    assert _memberships_by_group(eligibility_raw) == [
        frozenset(group) for group in memberships
    ]
    assert _tech_fields_by_group(eligibility_raw) == [
        frozenset(group) for group in tech_fields
    ]


@pytest.mark.parametrize(
    "eligibility_raw", [LICENSE_ONLY_RAW, MIXED_TWO_GROUP_RAW, MARINE_RAW]
)
def test_unconstrained_groups_stay_as_empty_frozensets(eligibility_raw):
    """해당 어휘를 요구하지 않는 그룹을 **걸러내지 않는다**.

    빈 그룹은 호출부가 "다른 자격 경로"(DEFER)로 읽는 신호다. 압축해 버리면 과차단
    회귀가 된다(license-axis 커버리지 비대칭 교훈).
    """
    rows = eligibility_raw["license_limits"]
    group_count = len({str(row.get("lmtGrpNo") or "") for row in rows})
    assert len(_memberships_by_group(eligibility_raw)) == group_count
    assert len(_tech_fields_by_group(eligibility_raw)) == group_count
