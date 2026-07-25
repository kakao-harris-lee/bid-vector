"""협회 가입(cohort) 자격 축 테스트.

``assess_association`` 순수 판정의 값 테이블(§4.5.3)과, 협회 요건이 **없는** 공고에
대한 classifier baseline 불변 특성화(회귀 방지)를 실 API/DB 접속 없이 검증한다.
공고 원문 샘플은 라이브 ``eligibility_raw["license_limits"]`` 실측 형태를 쓴다.
"""
from __future__ import annotations

import pytest

from app.models.models import CompanyProfile, Project
from app.services.classification import config
from app.services.classification.association import assess_association
from app.services.classifier import NoticeClassifierService

# --- 실측 형태 자격 원문 샘플 -------------------------------------------------

# 협회 가입을 참가자격에 명시한 공고(엔지니어링협회 canonical 로 매칭).
ASSOCIATION_REQUIRED_RAW = {
    "license_limits": [{"lcnsLmtNm": "한국엔지니어링협회 가입 업체", "lmtGrpNo": "1"}]
}

# 자격 데이터는 있으나 협회 요건은 없는 공고(면허제한만).
LICENSE_ONLY_RAW = {
    "license_limits": [{"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "1"}]
}

# 협회 어휘가 flags 에만 있는 공고 — flags 는 판정 소스가 아니라 요건 없음(중립).
FLAGS_ONLY_RAW = {"flags": {"note": "엔지니어링협회 관련 공고"}}

# 협회 요건이 한 그룹, 다른 그룹은 협회 무관 면허만인 다중 lmtGrpNo 공고.
# 그룹 간 OR + 협회 무제약 그룹(다른 자격 경로) → 미가입이어도 과차단 금지(DEFER).
# 이 형태가 #254(tech_field 평면-AND 과차단)와 동형인 협회 축 잠재 회귀를 재현한다.
ASSOC_OR_LICENSE_GROUPS_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "한국엔지니어링협회 가입 업체", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "해양엔지니어링/1001", "lmtGrpNo": "2"},
    ]
}

# 두 그룹 모두 협회 가입을 요구(그룹 내 AND·그룹 간 OR, 무제약 그룹 없음).
# 미가입 → 어느 그룹도 충족 못 함 → block(UNSATISFIED)이 다중 그룹에서도 유지된다.
BOTH_GROUPS_ASSOC_RAW = {
    "license_limits": [
        {"lcnsLmtNm": "한국엔지니어링협회 가입 업체", "lmtGrpNo": "1"},
        {"lcnsLmtNm": "엔지니어링협회 회원사", "lmtGrpNo": "2"},
    ]
}


def _profile(association_memberships: str = "") -> CompanyProfile:
    return CompanyProfile(
        business_type="engineering_service",
        license_codes="",
        region_codes="",
        annual_revenue=0.0,
        capacity_score=0.0,
        total_awards=0,
        association_memberships=association_memberships,
    )


def _project(eligibility_raw: dict | None) -> Project:
    return Project(
        title="해양 엔지니어링 용역",
        description="항만 설계 용역",
        requirements="",
        category="engineering_service",
        eligibility_raw=eligibility_raw,
    )


# --- assess_association 값 테이블 --------------------------------------------


@pytest.mark.parametrize(
    "eligibility_raw",
    [None, {}, LICENSE_ONLY_RAW, FLAGS_ONLY_RAW],
)
def test_no_requirement_is_neutral_pass_score_zero(eligibility_raw):
    """협회 요건이 없으면(없음/면허만/flags만) 중립 PASS·score 0.0·penalty 0.

    neutral score 가 0.0 이어야 baseline 이 불변한다(핵심 불변식). 프로필 가입
    여부와 무관하게 요건이 없으면 중립이다.
    """
    result = assess_association(_project(eligibility_raw), _profile())

    assert result.passed is True
    assert result.score == config.ASSOCIATION_NEUTRAL_SCORE == 0.0
    assert result.penalty == 0.0


def test_no_requirement_neutral_even_with_membership():
    # 프로필이 협회에 가입돼 있어도 공고 요건이 없으면 가점 없이 중립(요건이 축을 켠다).
    result = assess_association(_project(LICENSE_ONLY_RAW), _profile("한국엔지니어링협회"))

    assert result.passed is True
    assert result.score == 0.0
    assert result.penalty == 0.0


def test_requirement_with_membership_passes_with_score():
    result = assess_association(
        _project(ASSOCIATION_REQUIRED_RAW), _profile("한국엔지니어링협회")
    )

    assert result.passed is True
    assert result.score == config.ASSOCIATION_MATCH_SCORE
    assert result.score > 0.0
    assert result.penalty == 0.0
    assert any("협회 가입을 확인" in reason for reason in result.reasons)


def test_requirement_without_membership_fails_with_penalty():
    result = assess_association(_project(ASSOCIATION_REQUIRED_RAW), _profile(""))

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == config.ASSOCIATION_MISMATCH_PENALTY
    assert any("엔지니어링협회" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "membership_text",
    [
        "엔지니어링협회",  # canonical 정확 일치
        "엔지니어링 협회",  # 공백 변형
        "한국엔지니어링협회",  # 전체명칭 별칭
        "대한토목학회, 한국엔지니어링협회",  # 다중값 중 하나가 일치
    ],
)
def test_membership_normalization_and_aliases_match(membership_text):
    """공백·전체명칭·다중값 표기 차이가 요건 canonical 과 같은 어휘로 정규화돼 통과한다."""
    result = assess_association(
        _project(ASSOCIATION_REQUIRED_RAW), _profile(membership_text)
    )

    assert result.passed is True
    assert result.score == config.ASSOCIATION_MATCH_SCORE


def test_requirement_with_unrelated_membership_fails():
    # 다른 협회만 가입한 프로필은 요구 협회 미보유로 걸린다.
    result = assess_association(
        _project(ASSOCIATION_REQUIRED_RAW), _profile("대한토목학회")
    )

    assert result.passed is False
    assert result.penalty == config.ASSOCIATION_MISMATCH_PENALTY


# --- 그룹 인지(lmtGrpNo OR/AND) 값 테이블 — #254 동형 회귀 선제 차단 ------------


def test_unconstrained_group_defers_non_member_instead_of_blocking():
    """협회 무제약 그룹(다른 자격 경로)이 있으면 미가입이어도 과차단하지 않고 중립.

    이것이 이관의 핵심 완화: 과거 평면-AND 는 협회 요건을 전체 AND 로 봐 다른 그룹의
    비-협회 자격 경로를 무시하고 미가입 operator 를 차단했다(#254 tech_field 와 동형).
    그룹-OR/DEFER 로 정렬하면 그 경로가 성립할 수 있어 협회 축은 defer 한다.
    """
    result = assess_association(_project(ASSOC_OR_LICENSE_GROUPS_RAW), _profile(""))

    assert result.passed is True
    assert result.score == config.ASSOCIATION_NEUTRAL_SCORE == 0.0
    assert result.penalty == 0.0
    assert any("다른 그룹" in reason for reason in result.reasons)


def test_member_satisfies_via_one_group_when_multiple_groups():
    """다중 그룹 공고에서 어느 한 그룹의 요구 협회를 보유하면 통과(그룹 간 OR)."""
    result = assess_association(
        _project(ASSOC_OR_LICENSE_GROUPS_RAW), _profile("한국엔지니어링협회")
    )

    assert result.passed is True
    assert result.score == config.ASSOCIATION_MATCH_SCORE
    assert result.penalty == 0.0


def test_all_groups_association_constrained_still_blocks_non_member():
    """모든 그룹이 협회를 요구(무제약 경로 없음)하면 미가입은 다중 그룹에서도 차단."""
    result = assess_association(_project(BOTH_GROUPS_ASSOC_RAW), _profile(""))

    assert result.passed is False
    assert result.score == 0.0
    assert result.penalty == config.ASSOCIATION_MISMATCH_PENALTY
    assert any("엔지니어링협회" in reason for reason in result.reasons)


def test_all_groups_association_constrained_passes_member():
    """모든 그룹이 협회를 요구해도 가입 operator 는 각 그룹을 충족해 통과."""
    result = assess_association(
        _project(BOTH_GROUPS_ASSOC_RAW), _profile("한국엔지니어링협회")
    )

    assert result.passed is True
    assert result.score == config.ASSOCIATION_MATCH_SCORE
    assert result.penalty == 0.0


# --- classifier 특성화: 협회 요건 없는 공고 baseline 불변 (CRITICAL 회귀 가드) ---


def _classify_no_semantic(service, project, profile):
    """의미 유사도 모델 로딩을 피해 결정적으로 classify (다른 축은 그대로)."""
    service._compute_semantic_similarity = lambda project_text, profile_text: (
        0.5,
        "mock-embedding",
    )
    return service.classify(project=project, profile=profile)


def _score_excluding_association(criteria: dict) -> float:
    """association 축을 제외한 나머지 축만으로 최종 score 를 재계산한다(축 추가 전 값)."""
    positive = sum(c["score"] for axis, c in criteria.items() if axis != "association")
    penalty = sum(c["penalty"] for axis, c in criteria.items() if axis != "association")
    return round(max(0.0, min(1.0, positive - penalty)), 2)


@pytest.mark.parametrize(
    "eligibility_raw",
    [None, LICENSE_ONLY_RAW, FLAGS_ONLY_RAW],
)
def test_classify_baseline_unchanged_when_no_association_requirement(eligibility_raw):
    """협회 요건 없는 공고의 score·matched·blocking_axes 가 축 추가 전과 동일하다.

    불변 증명 방식: neutral 축의 score/penalty 가 정확히 0.0 이고 passed=True 이므로
    (1) 최종 score = clamp(Σpositive − Σpenalty) 에서 association 항의 기여가 0,
    (2) matched = all(passed) 에서 association 이 항상 통과,
    (3) blocking_axes(=탈락 축)에 association 이 들어가지 않는다.
    => association 을 제외하고 재계산한 score 와 실제 score 가 같아야 하고,
       association 은 blocking_axes 에 없어야 한다. criteria 에 축이 추가되는 것만 허용.
    """
    service = NoticeClassifierService()
    profile = _profile()  # 협회 미가입 프로필도 요건이 없으면 중립이어야 한다.
    project = _project(eligibility_raw)

    result = _classify_no_semantic(service, project, profile)

    assoc = result["criteria"]["association"]
    assert assoc["score"] == 0.0
    assert assoc["penalty"] == 0.0
    assert assoc["passed"] is True
    assert "association" not in result["score_breakdown"]["blocking_axes"]
    # association 을 뺀 재계산 score 와 실제 final score 가 byte-identical.
    assert result["score"] == _score_excluding_association(result["criteria"])


def test_classify_blocks_when_association_required_and_missing():
    """협회 요건 공고에서 미가입 operator 는 association 이 blocking·matched=False."""
    service = NoticeClassifierService()
    result = _classify_no_semantic(
        service, _project(ASSOCIATION_REQUIRED_RAW), _profile("")
    )

    assert result["criteria"]["association"]["passed"] is False
    assert result["criteria"]["association"]["penalty"] == config.ASSOCIATION_MISMATCH_PENALTY
    assert "association" in result["score_breakdown"]["blocking_axes"]
    assert result["matched"] is False


def test_classify_passes_association_when_operator_is_member():
    """협회 요건 공고에서 가입 operator 는 association 통과·blocking 미포함."""
    service = NoticeClassifierService()
    result = _classify_no_semantic(
        service, _project(ASSOCIATION_REQUIRED_RAW), _profile("한국엔지니어링협회")
    )

    assert result["criteria"]["association"]["passed"] is True
    assert result["criteria"]["association"]["score"] == config.ASSOCIATION_MATCH_SCORE
    assert "association" not in result["score_breakdown"]["blocking_axes"]
