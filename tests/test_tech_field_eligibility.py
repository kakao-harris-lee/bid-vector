"""기술부문(cohort) 자격 축 테스트.

``assess_tech_field`` 순수 판정의 값 테이블(§4.5.3)과, 기술부문 요건이 **없는**
공고에 대한 classifier baseline 불변 특성화(회귀 방지)를 실 API/DB 접속 없이
검증한다. 공고 원문 샘플은 라이브 ``eligibility_raw["license_limits"]`` 실측 면허명
형태(``엔지니어링사업(해양)``·``기술사사무소(해양)``·``해양조사정보업(수로측량업)``)를 쓴다.
code↔name 정합(#231): 요건 측(``required_tech_fields``)과 프로필 측
(``match_tech_field_terms``)이 같은 표준명 어휘로 정규화됨을 함께 고정한다.
"""
from __future__ import annotations

import pytest

from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.tech_field import assess_tech_field
from app.services.classifier import NoticeClassifierService

# --- 실측 형태 자격 원문 샘플 -------------------------------------------------

# 해양엔지니어링(MAR001) 을 참가자격에 명시한 공고 — 표준명 "해양엔지니어링" 요건.
MARINE_REQUIRED_RAW = {
    "license_limits": [{"lcnsLmtNm": "엔지니어링사업(해양)", "lmtGrpNo": "1"}]
}
# 항만및해안(PORT001) 요건 — 여는 괄호 앵커라 comma/가운뎃점 구분자 무관.
PORT_REQUIRED_RAW = {
    "license_limits": [{"lcnsLmtNm": "엔지니어링사업(항만, 해안)", "lmtGrpNo": "1"}]
}
# 수로조사(HYDRO001) 요건 — "수로측량" 별칭이 흡수해 표준명 "수로조사" 로 수렴.
HYDRO_REQUIRED_RAW = {
    "license_limits": [{"lcnsLmtNm": "해양조사정보업(수로측량업)", "lmtGrpNo": "1"}]
}
# 해양기술사(MAR001) 요건 — 표준명이 자기 별칭 목록에 없는(name∉aliases) 항목이라
# 프로필 측 round-trip(name 토큰 포함)을 검증하는 케이스.
TECHSA_REQUIRED_RAW = {
    "license_limits": [{"lcnsLmtNm": "기술사사무소(해양)", "lmtGrpNo": "1"}]
}

# 자격 데이터는 있으나 기술부문 요건은 없는 공고(비해양 면허제한만).
LICENSE_ONLY_RAW = {
    "license_limits": [{"lcnsLmtNm": "토목공사업/1001", "lmtGrpNo": "1"}]
}
# 기술부문 어휘가 flags 에만 있는 공고 — flags 는 판정 소스가 아니라 요건 없음(중립).
FLAGS_ONLY_RAW = {"flags": {"note": "해양엔지니어링 관련 공고"}}

# --- 다중 그룹(lmtGrpNo) 실측 형태 -------------------------------------------
# 라이브 R26BK01642740/642657/642363/642337 (연근해어장 생산성 개선 현장지원용역):
# 그룹1 = 엔지니어링사업(해양)/3599 → 해양엔지니어링, 그룹2 = 기술사사무소(해양)/7383
# → 해양기술사. lmtGrpNo 가 다르므로 그룹 간 OR(하나만 충족해도 참가 가능).
TWO_GROUP_OR_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "엔지니어링사업(해양)/3599", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "기술사사무소(해양)/7383", "lmtGrpNo": "2"},
    ]
}
# 그룹1 = 해양엔지니어링(기술부문 요구), 그룹2 = 토목공사업(기술부문 무매핑=무제약).
# 그룹2 는 tech_field 가 검증할 수 없는 다른 자격 경로 → 과차단 금지(defer=중립).
CONSTRAINED_PLUS_UNCONSTRAINED_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "엔지니어링사업(해양)/3599", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "토목공사업/1001", "lmtGrpNo": "2"},
    ]
}
# 단일 그룹(lmtGrpNo 동일) 내 두 기술부문 = AND(둘 다 필요). 기존 평면 AND 와 동치.
SINGLE_GROUP_AND_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "엔지니어링사업(해양)/3599", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "엔지니어링사업(항만, 해안)/3579", "lmtGrpNo": "1"},
    ]
}
# lcnsLmtNm 은 비-tech(토목), permsnIndstrytyList 만 해양 기술부문 어휘를 담음.
# 면허 게이트는 permsnIndstrytyList 를 판정에 쓰지 않으므로(lcnsLmtNm 만) 이 축도
# 게이트와 정렬해 요건 없음(중립)으로 본다. 과거 평면 경로는 이를 요건으로 잡아
# 미보유 시 BLOCK 했다 — 소스 정렬로 완화되는 divergence 를 고정한다.
PERMSN_ONLY_TECH_RAW = {
    "license_limits": [
        {
            "lcnsLmtNm": "토목공사업/1001",
            "permsnIndstrytyList": "엔지니어링사업(해양)",
            "lmtGrpNo": "1",
        }
    ]
}
# lmtGrpNo 결측 행들 → parse_license_limit_groups 가 UNGROUPED 단일 그룹으로 폴딩.
# 단일 그룹 내 AND 라 둘 다 있어야 통과(그룹핑 위임의 결측 폴딩 회귀 가드).
MISSING_GRP_NO_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "엔지니어링사업(해양)/3599"},
        {"lcnsLmtNm": "기술사사무소(해양)/7383"},
    ]
}
# 두 그룹 모두 비-tech 면허(기술부문 무매핑) → constrained 없음 → 중립 PASS.
ALL_NON_TECH_GROUPS_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "토목공사업/1001", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "건축공사업/1002", "lmtGrpNo": "2"},
    ]
}


def _profile(tech_fields: str = "") -> CompanyProfile:
    return CompanyProfile(
        business_type="engineering_service",
        license_codes="",
        region_codes="",
        annual_revenue=0.0,
        capacity_score=0.0,
        total_awards=0,
        tech_fields=tech_fields,
    )


def _project(eligibility_raw: dict | None) -> Project:
    return Project(
        title="해양 엔지니어링 용역",
        description="항만 설계 용역",
        requirements="",
        category="engineering_service",
        eligibility_raw=eligibility_raw,
    )


# --- assess_tech_field 값 테이블 ---------------------------------------------


@pytest.mark.parametrize(
    "eligibility_raw",
    [None, {}, LICENSE_ONLY_RAW, FLAGS_ONLY_RAW],
)
def test_no_requirement_is_neutral_pass_score_zero(eligibility_raw):
    """기술부문 요건이 없으면(없음/비해양 면허만/flags만) 중립 PASS·score 0.0·penalty 0.

    neutral score 가 0.0 이어야 baseline 이 불변한다(핵심 불변식). 프로필 보유
    여부와 무관하게 요건이 없으면 중립이다.
    """
    result = assess_tech_field(_project(eligibility_raw), _profile())

    assert result.passed is True
    assert result.score == config.TECH_FIELD_NEUTRAL_SCORE == 0.0
    assert result.penalty == 0.0


def test_no_requirement_neutral_even_with_tech_field():
    # 프로필이 기술부문을 보유해도 공고 요건이 없으면 가점 없이 중립(요건이 축을 켠다).
    result = assess_tech_field(_project(LICENSE_ONLY_RAW), _profile("해양엔지니어링"))

    assert result.passed is True
    assert result.score == 0.0
    assert result.penalty == 0.0


def test_requirement_with_holding_passes_with_score():
    result = assess_tech_field(
        _project(MARINE_REQUIRED_RAW), _profile("해양엔지니어링")
    )

    assert result.passed is True
    assert result.score == config.TECH_FIELD_MATCH_SCORE
    assert result.score > 0.0
    assert result.penalty == 0.0
    assert any("기술부문을 확인" in reason for reason in result.reasons)


def test_requirement_without_holding_fails_with_penalty():
    result = assess_tech_field(_project(MARINE_REQUIRED_RAW), _profile(""))

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == config.TECH_FIELD_MISMATCH_PENALTY
    assert any("해양엔지니어링" in reason for reason in result.reasons)


def test_requirement_with_unrelated_tech_field_fails():
    # 다른 기술부문(항만)만 보유한 프로필은 요구 기술부문(해양) 미보유로 걸린다.
    result = assess_tech_field(_project(MARINE_REQUIRED_RAW), _profile("항만및해안"))

    assert result.passed is False
    assert result.penalty == config.TECH_FIELD_MISMATCH_PENALTY


@pytest.mark.parametrize(
    ("eligibility_raw", "profile_text", "expected_term"),
    [
        # 표준명 정확 일치(code↔name 정합: 요건·프로필 모두 표준명 축).
        (PORT_REQUIRED_RAW, "항만및해안", "항만및해안"),
        # 공백 변형 — _normalize 가 흡수.
        (PORT_REQUIRED_RAW, "항만 및 해안", "항만및해안"),
        # 별칭(수로측량) 이 요건 표준명(수로조사)과 같은 어휘로 정규화.
        (HYDRO_REQUIRED_RAW, "수로측량", "수로조사"),
        # 면허 원문 형태를 프로필에 그대로 저장한 경우도 표준명으로 수렴.
        (MARINE_REQUIRED_RAW, "엔지니어링사업(해양)", "해양엔지니어링"),
        # name∉aliases 항목(해양기술사) 의 표준명 round-trip.
        (TECHSA_REQUIRED_RAW, "해양기술사", "해양기술사"),
        # 다중값 중 하나가 일치.
        (MARINE_REQUIRED_RAW, "대한토목학회, 해양엔지니어링", "해양엔지니어링"),
    ],
)
def test_normalization_and_alias_match(eligibility_raw, profile_text, expected_term):
    """표면 변형·별칭·면허원문·name∉aliases 가 요건 표준명과 같은 어휘로 정규화돼 통과한다."""
    result = assess_tech_field(_project(eligibility_raw), _profile(profile_text))

    assert result.passed is True
    assert result.score == config.TECH_FIELD_MATCH_SCORE
    assert any(expected_term in reason for reason in result.reasons)


# --- 그룹(lmtGrpNo) 인지 값 테이블: 면허 게이트와 OR/AND 정렬 --------------------


def test_two_group_or_holding_one_group_passes():
    """다중 그룹 OR — 그룹1(해양엔지니어링)만 보유·그룹2(해양기술사) 미보유 → PASS.

    핵심 회귀 가드: 라이브 R26BK01642740 패턴. 면허 게이트가 그룹1 충족으로 eligible
    을 낸 operator 를 tech_field 축이 과차단하던 두 축 모순을 재현·방지한다. 그룹 간
    OR 이므로 한 그룹만 전부 보유해도 통과한다.
    """
    result = assess_tech_field(_project(TWO_GROUP_OR_RAW), _profile("해양엔지니어링"))

    assert result.passed is True
    assert result.score == config.TECH_FIELD_MATCH_SCORE
    assert result.penalty == 0.0
    assert any("해양엔지니어링" in reason for reason in result.reasons)


def test_two_group_or_holding_neither_blocks():
    """다중 그룹, 어느 그룹도 전부 보유하지 못하면 BLOCK(penalty)."""
    result = assess_tech_field(_project(TWO_GROUP_OR_RAW), _profile("항만및해안"))

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == config.TECH_FIELD_MISMATCH_PENALTY
    # 그룹별 요구(OR)가 reason 에 나열된다.
    assert any("해양엔지니어링" in reason for reason in result.reasons)
    assert any("해양기술사" in reason for reason in result.reasons)


def test_single_group_and_missing_one_blocks_unchanged():
    """단일 그룹 내 2 기술부문 AND — 하나만 보유하면 BLOCK(기존 평면 AND 동작 불변).

    lmtGrpNo 가 같으면 그룹 내 AND 이므로 둘 다 있어야 통과한다. 이 동작은 그룹
    인지 이전(평면 합산 후 전체 AND)과 완전히 동일하다.
    """
    result = assess_tech_field(
        _project(SINGLE_GROUP_AND_RAW), _profile("해양엔지니어링")
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == config.TECH_FIELD_MISMATCH_PENALTY


def test_single_group_and_holding_all_passes():
    """단일 그룹 내 2 기술부문 AND — 둘 다 보유하면 PASS(MATCH_SCORE)."""
    result = assess_tech_field(
        _project(SINGLE_GROUP_AND_RAW), _profile("해양엔지니어링, 항만및해안")
    )

    assert result.passed is True
    assert result.score == config.TECH_FIELD_MATCH_SCORE
    assert result.penalty == 0.0
    assert any("해양엔지니어링" in reason for reason in result.reasons)
    assert any("항만및해안" in reason for reason in result.reasons)


def test_unconstrained_group_defers_to_neutral():
    """기술부문 무제약 그룹(다른 자격 경로) 존재 시 미보유여도 과차단하지 않고 중립.

    그룹1 = 해양엔지니어링(요구·미보유), 그룹2 = 토목공사업(기술부문 무매핑=무제약).
    tech_field 는 그룹2 의 비-기술 면허를 검증할 수 없으므로 defer(중립 PASS)한다.
    """
    result = assess_tech_field(
        _project(CONSTRAINED_PLUS_UNCONSTRAINED_RAW), _profile("")
    )

    assert result.passed is True
    assert result.score == config.TECH_FIELD_NEUTRAL_SCORE == 0.0
    assert result.penalty == 0.0


def test_unconstrained_group_passes_with_score_when_constrained_held():
    """무제약 그룹이 있어도, 요구 그룹을 실제 보유하면 중립이 아니라 MATCH 로 통과."""
    result = assess_tech_field(
        _project(CONSTRAINED_PLUS_UNCONSTRAINED_RAW), _profile("해양엔지니어링")
    )

    assert result.passed is True
    assert result.score == config.TECH_FIELD_MATCH_SCORE
    assert result.penalty == 0.0


def test_permsn_indstryty_only_requirement_is_neutral_gate_aligned():
    """요건이 permsnIndstrytyList 로만 표현되면 게이트 정렬로 중립(요건 소스=lcnsLmtNm).

    면허 게이트(parse_license_limit_groups)는 permsnIndstrytyList 를 판정에 쓰지
    않으므로 이 축도 lcnsLmtNm 만 요건 소스로 본다. 과거 평면 경로는 이를 요건으로
    잡아 미보유 시 BLOCK 했다 — 소스 정렬로 완화되는 방향을 고정하는 특성화.
    """
    result = assess_tech_field(_project(PERMSN_ONLY_TECH_RAW), _profile(""))

    assert result.passed is True
    assert result.score == config.TECH_FIELD_NEUTRAL_SCORE == 0.0
    assert result.penalty == 0.0


def test_missing_lmt_grp_no_folds_to_single_and_group():
    """lmtGrpNo 결측 행들은 UNGROUPED 단일 그룹(AND)으로 폴딩 — 하나만 보유 시 BLOCK."""
    result = assess_tech_field(_project(MISSING_GRP_NO_RAW), _profile("해양엔지니어링"))

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == config.TECH_FIELD_MISMATCH_PENALTY


def test_all_non_tech_groups_are_neutral():
    """모든 그룹이 비-tech 면허(기술부문 무매핑)면 요건 없음(중립 PASS)."""
    result = assess_tech_field(_project(ALL_NON_TECH_GROUPS_RAW), _profile(""))

    assert result.passed is True
    assert result.score == config.TECH_FIELD_NEUTRAL_SCORE
    assert result.penalty == 0.0


# --- classifier 특성화: 기술부문 요건 없는 공고 baseline 불변 (CRITICAL 회귀 가드) ---


def _classify_no_semantic(service, project, profile):
    """의미 유사도 모델 로딩을 피해 결정적으로 classify (다른 축은 그대로)."""
    service._compute_semantic_similarity = lambda project_text, profile_text: (
        0.5,
        "mock-embedding",
    )
    return service.classify(project=project, profile=profile)


def _score_excluding_tech_field(criteria: dict) -> float:
    """tech_field 축을 제외한 나머지 축만으로 최종 score 를 재계산한다(축 추가 전 값)."""
    positive = sum(c["score"] for axis, c in criteria.items() if axis != "tech_field")
    penalty = sum(c["penalty"] for axis, c in criteria.items() if axis != "tech_field")
    return round(max(0.0, min(1.0, positive - penalty)), 2)


@pytest.mark.parametrize(
    "eligibility_raw",
    [None, LICENSE_ONLY_RAW, FLAGS_ONLY_RAW],
)
def test_classify_baseline_unchanged_when_no_tech_field_requirement(eligibility_raw):
    """기술부문 요건 없는 공고의 score·matched·blocking_axes 가 축 추가 전과 동일하다.

    불변 증명 방식: neutral 축의 score/penalty 가 정확히 0.0 이고 passed=True 이므로
    (1) 최종 score = clamp(Σpositive − Σpenalty) 에서 tech_field 항의 기여가 0,
    (2) matched = all(passed) 에서 tech_field 가 항상 통과,
    (3) blocking_axes(=탈락 축)에 tech_field 가 들어가지 않는다.
    => tech_field 를 제외하고 재계산한 score 와 실제 score 가 같아야 하고,
       tech_field 는 blocking_axes 에 없어야 한다. criteria 에 축이 추가되는 것만 허용.
    """
    service = NoticeClassifierService()
    profile = _profile()  # 기술부문 미보유 프로필도 요건이 없으면 중립이어야 한다.
    project = _project(eligibility_raw)

    result = _classify_no_semantic(service, project, profile)

    tech = result["criteria"]["tech_field"]
    assert tech["score"] == 0.0
    assert tech["penalty"] == 0.0
    assert tech["passed"] is True
    assert "tech_field" not in result["score_breakdown"]["blocking_axes"]
    # tech_field 를 뺀 재계산 score 와 실제 final score 가 byte-identical.
    assert result["score"] == _score_excluding_tech_field(result["criteria"])


def test_classify_blocks_when_tech_field_required_and_missing():
    """기술부문 요건 공고에서 미보유 operator 는 tech_field 가 blocking·matched=False."""
    service = NoticeClassifierService()
    result = _classify_no_semantic(
        service, _project(MARINE_REQUIRED_RAW), _profile("")
    )

    assert result["criteria"]["tech_field"]["passed"] is False
    assert (
        result["criteria"]["tech_field"]["penalty"]
        == config.TECH_FIELD_MISMATCH_PENALTY
    )
    assert "tech_field" in result["score_breakdown"]["blocking_axes"]
    assert result["matched"] is False


def test_classify_passes_tech_field_when_operator_holds():
    """기술부문 요건 공고에서 보유 operator 는 tech_field 통과·blocking 미포함."""
    service = NoticeClassifierService()
    result = _classify_no_semantic(
        service, _project(MARINE_REQUIRED_RAW), _profile("해양엔지니어링")
    )

    assert result["criteria"]["tech_field"]["passed"] is True
    assert (
        result["criteria"]["tech_field"]["score"] == config.TECH_FIELD_MATCH_SCORE
    )
    assert "tech_field" not in result["score_breakdown"]["blocking_axes"]
