"""온보딩 후보 역추천 서비스·엔드포인트 테스트.

두 층으로 고정한다:
- **순수 집계**(:func:`aggregate_suggestions`)를 :class:`NoticeFeatures` 값
  테이블로 검증한다(DB 없이). 임계·confidence 밴드·후보 없음 진단 포함.
- **엔드포인트**(``GET /api/v1/operator/onboarding-suggestions``)는 fixture
  Project 를 심어 후보가 나오는지, ``needs_confirmation`` 이 항상 True 인지,
  매칭 0건이면 빈 후보 + 원인 진단이 나오는지, 키워드 누락이 422 인지 검증한다.

정직 명세(§2): 이 경로는 후보를 어디에도 persist 하지 않는다 — 테스트도 저장을
검증하지 않고 반환만 본다.
"""
from __future__ import annotations

from app.models.models import CompanyProfile, OperatorStrategy, Project
from app.services.onboarding.suggestions import (
    BUDGET_ROUNDING_UNIT,
    CONFIDENCE_BANDS,
    FIELD_BUSINESS_TYPE,
    FIELD_FOCUS_CATEGORIES,
    FIELD_FOCUS_REGIONS,
    FIELD_LICENSE_CODES,
    FIELD_MAX_BUDGET,
    FIELD_MIN_BUDGET,
    FIELD_REGION_CODES,
    MIN_SUPPORTING_NOTICES,
    NEEDS_CONFIRMATION,
    SOURCE_INTERNAL_NOTICES,
    NoticeFeatures,
    OnboardingSeed,
    aggregate_suggestions,
    project_to_features,
)

_SEED = OnboardingSeed(keywords=("항만",))


def _feature(
    *,
    business_type="construction",
    category="construction",
    license_codes=(),
    region_codes=(),
    budget=None,
):
    return NoticeFeatures(
        business_type=business_type,
        category=category,
        license_codes=frozenset(license_codes),
        region_codes=frozenset(region_codes),
        budget=budget,
    )


def _by_field(suggestions, field):
    for item in suggestions:
        if item.field == field:
            return item
    return None


# --- 순수 집계: 후보 없음 / 임계 ---------------------------------------------


def test_empty_features_yield_no_candidates_with_diagnostic():
    """매칭 0건 → 빈 후보 + 데이터 부족/키워드 협소 진단(설계 §4)."""
    bundle = aggregate_suggestions([], seed=_SEED)

    assert bundle.matched_notice_count == 0
    assert bundle.profile == []
    assert bundle.strategy == []
    assert "매칭되는 내부 공고가 없습니다" in bundle.diagnostics


def test_matched_but_below_threshold_yields_no_candidates():
    """매칭은 됐으나 어떤 값도 최소 지지 미달 → 빈 후보 + 과필터링/희소 진단."""
    # 단발 신호만 있는 두 공고: 각기 다른 면허/카테고리라 어느 값도 2건 지지 없음.
    features = [
        _feature(business_type="construction", category="construction", license_codes=("CIV001",)),
        _feature(business_type="service", category="service", license_codes=("SW001",)),
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    assert bundle.matched_notice_count == 2
    # business_type: construction·service 각 1건 → 최다 1건 < MIN_SUPPORTING_NOTICES.
    assert bundle.profile == []
    assert bundle.strategy == []
    assert str(MIN_SUPPORTING_NOTICES) in bundle.diagnostics


# --- 순수 집계: 각 후보 도출 -------------------------------------------------


def test_dominant_business_type_candidate():
    """최다 분포 업무구분이 단일 business_type 후보로 나온다."""
    features = [
        _feature(business_type="construction"),
        _feature(business_type="construction"),
        _feature(business_type="service"),
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    suggestion = _by_field(bundle.profile, FIELD_BUSINESS_TYPE)
    assert suggestion is not None
    assert suggestion.value == "construction"
    assert suggestion.source == SOURCE_INTERNAL_NOTICES
    assert suggestion.needs_confirmation is True
    assert suggestion.matched_notice_count == 2
    # 지지비율 2/3 ≈ 0.667 → 최상위 밴드(0.8).
    assert suggestion.confidence == CONFIDENCE_BANDS[0][1]


def test_license_codes_candidate_respects_min_support():
    """면허 코드는 최소 지지(2건) 이상만 후보에 포함된다."""
    features = [
        _feature(license_codes=("CIV001",)),
        _feature(license_codes=("CIV001",)),
        _feature(license_codes=("PORT001",)),  # 1건뿐 → 제외
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    suggestion = _by_field(bundle.profile, FIELD_LICENSE_CODES)
    assert suggestion is not None
    assert suggestion.value == ["CIV001"]
    assert "PORT001" not in suggestion.value
    assert suggestion.matched_notice_count == 2
    assert "CIV001(2건)" in suggestion.reason


def test_region_candidate_only_from_restricted_notices():
    """지역제한이 명시된 공고의 지역만 region_codes/focus_regions 후보로 나온다."""
    features = [
        _feature(region_codes=("부산",)),
        _feature(region_codes=("부산",)),
        _feature(region_codes=()),  # 지역제한 없음 → 신호 없음
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    profile_region = _by_field(bundle.profile, FIELD_REGION_CODES)
    strategy_region = _by_field(bundle.strategy, FIELD_FOCUS_REGIONS)
    assert profile_region is not None and profile_region.value == ["부산"]
    assert strategy_region is not None and strategy_region.value == ["부산"]
    assert profile_region.matched_notice_count == 2


def test_focus_categories_candidate():
    """카테고리 분포에서 focus_categories 후보(다중값)가 나온다."""
    features = [
        _feature(category="construction"),
        _feature(category="construction"),
        _feature(category="service"),
        _feature(category="service"),
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    suggestion = _by_field(bundle.strategy, FIELD_FOCUS_CATEGORIES)
    assert suggestion is not None
    assert set(suggestion.value) == {"construction", "service"}


def test_budget_min_max_candidates_from_distribution():
    """예산 표본이 충분하면 min/max_budget_estimate 후보가 만원 단위 envelope 로."""
    budgets = [100_000_000, 200_000_000, 300_000_000, 400_000_000, 500_000_000]
    features = [_feature(budget=value) for value in budgets]
    bundle = aggregate_suggestions(features, seed=_SEED)

    low = _by_field(bundle.strategy, FIELD_MIN_BUDGET)
    high = _by_field(bundle.strategy, FIELD_MAX_BUDGET)
    assert low is not None and high is not None
    assert low.value <= high.value
    # 만원 단위로 반올림(내림/올림)됐다.
    assert low.value % BUDGET_ROUNDING_UNIT == 0
    assert high.value % BUDGET_ROUNDING_UNIT == 0
    assert low.matched_notice_count == len(budgets)


def test_budget_needs_minimum_samples():
    """예산 표본이 최소치 미만이면 예산 후보를 내지 않는다."""
    features = [_feature(budget=100_000_000), _feature(budget=200_000_000)]
    bundle = aggregate_suggestions(features, seed=_SEED)

    assert _by_field(bundle.strategy, FIELD_MIN_BUDGET) is None
    assert _by_field(bundle.strategy, FIELD_MAX_BUDGET) is None


def test_all_candidates_need_confirmation_and_bounded_confidence():
    """모든 후보는 needs_confirmation=True 이고 confidence 는 1.0 미만이다(§2 정직)."""
    features = [
        _feature(license_codes=("CIV001",), region_codes=("부산",), budget=100_000_000),
        _feature(license_codes=("CIV001",), region_codes=("부산",), budget=200_000_000),
        _feature(license_codes=("CIV001",), region_codes=("부산",), budget=300_000_000),
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    all_items = bundle.profile + bundle.strategy
    assert all_items, "후보가 하나 이상 나와야 한다"
    for item in all_items:
        assert item.needs_confirmation is NEEDS_CONFIRMATION is True
        assert 0.0 <= item.confidence < 1.0
        assert item.source == SOURCE_INTERNAL_NOTICES


# --- 특성 추출(project_to_features) ------------------------------------------


def test_project_to_features_extracts_license_codes_from_eligibility_raw():
    """eligibility_raw.license_limits 의 lcnsLmtNm 에서 면허 코드를 추출한다."""
    project = Project(
        title="항만 준설 공사",
        description="",
        requirements="",
        budget_estimate=500_000_000.0,
        category="construction",
        eligibility_raw={
            "flags": {},
            "license_limits": [{"lcnsLmtNm": "토목공사업", "lmtGrpNo": "1"}],
        },
    )
    features = project_to_features(project)

    assert "CIV001" in features.license_codes
    assert features.business_type == "construction"
    assert features.budget == 500_000_000.0


def test_project_to_features_region_requires_strict_limit():
    """지역명이 있어도 지역제한 키워드가 없으면 region 신호를 비운다."""
    unrestricted = Project(
        title="항만 시설 용역",
        description="부산 지역 항만 시설",  # 지역명만, 제한 키워드 없음
        requirements="",
        budget_estimate=100_000_000.0,
        category="service",
    )
    restricted = Project(
        title="항만 시설 용역",
        description="",
        requirements="부산 소재업체만 입찰 가능",  # 제한 키워드 있음
        budget_estimate=100_000_000.0,
        category="service",
    )

    assert project_to_features(unrestricted).region_codes == frozenset()
    assert project_to_features(restricted).region_codes == frozenset({"부산"})


# --- 엔드포인트 --------------------------------------------------------------


def _make_notice(
    test_db,
    *,
    title,
    category,
    requirements="",
    description="",
    budget_estimate=100_000_000.0,
    eligibility_raw=None,
):
    project = Project(
        title=title,
        description=description,
        requirements=requirements,
        budget_estimate=budget_estimate,
        category=category,
        eligibility_raw=eligibility_raw,
    )
    test_db.add(project)
    test_db.flush()
    test_db.commit()
    return project.id


def _seed_marine_notices(test_db):
    """'항만' 매칭 공고 3건 + 비매칭 1건을 심는다."""
    _make_notice(
        test_db,
        title="부산항 항만 준설 공사",
        category="construction",
        requirements="부산 소재업체만 입찰 가능",
        budget_estimate=500_000_000.0,
        eligibility_raw={"flags": {}, "license_limits": [{"lcnsLmtNm": "토목공사업"}]},
    )
    _make_notice(
        test_db,
        title="항만 방파제 보강 공사",
        category="construction",
        requirements="부산 소재업체만 참여 가능",
        budget_estimate=800_000_000.0,
        eligibility_raw={"flags": {}, "license_limits": [{"lcnsLmtNm": "토목공사업"}]},
    )
    _make_notice(
        test_db,
        title="항만 설계 용역",
        category="construction",
        budget_estimate=300_000_000.0,
        eligibility_raw={"flags": {}, "license_limits": [{"lcnsLmtNm": "항만및해안"}]},
    )
    # 비매칭(키워드 '항만' 없음) — 매칭 카운트에 들어가면 안 된다.
    _make_notice(test_db, title="전산 장비 구매", category="goods")


def test_endpoint_returns_candidates(client, test_db):
    """매칭 공고가 있으면 프로필/전략 후보를 반환하고 needs_confirmation 은 항상 True."""
    _seed_marine_notices(test_db)

    response = client.get(
        "/api/v1/operator/onboarding-suggestions", params={"keywords": "항만"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["keywords"] == ["항만"]
    assert payload["matched_notice_count"] == 3  # 비매칭 goods 는 제외
    assert payload["current_operator_id"] >= 1

    fields = {item["field"] for item in payload["profile"] + payload["strategy"]}
    assert FIELD_BUSINESS_TYPE in fields
    assert FIELD_LICENSE_CODES in fields
    assert FIELD_FOCUS_CATEGORIES in fields

    for item in payload["profile"] + payload["strategy"]:
        assert item["needs_confirmation"] is True
        assert item["source"] == SOURCE_INTERNAL_NOTICES
        assert 0.0 <= item["confidence"] < 1.0

    # 면허 후보는 2건 지지된 CIV001 만(1건짜리 PORT001 은 제외).
    license_item = _endpoint_field(payload["profile"], FIELD_LICENSE_CODES)
    assert license_item["value"] == ["CIV001"]


def test_endpoint_no_match_returns_empty_with_diagnostic(client, test_db):
    """매칭 0건이면 빈 후보 + 데이터 부족 진단을 반환한다(설계 §4)."""
    _seed_marine_notices(test_db)

    response = client.get(
        "/api/v1/operator/onboarding-suggestions",
        params={"keywords": "존재하지않는키워드XYZ"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["matched_notice_count"] == 0
    assert payload["profile"] == []
    assert payload["strategy"] == []
    assert "매칭되는 내부 공고가 없습니다" in payload["diagnostics"]


def test_endpoint_requires_keywords(client, test_db):
    """키워드 없이 호출하면 422."""
    response = client.get("/api/v1/operator/onboarding-suggestions")
    assert response.status_code == 422


def test_endpoint_does_not_persist_profile_or_strategy(client, test_db):
    """후보 조회는 CompanyProfile/OperatorStrategy 를 만들거나 바꾸지 않는다(§2)."""
    _seed_marine_notices(test_db)

    response = client.get(
        "/api/v1/operator/onboarding-suggestions", params={"keywords": "항만"}
    )
    assert response.status_code == 200

    # 후보 조회로 프로필/전략에 확정값이 새로 써지면 안 된다.
    assert test_db.query(CompanyProfile).count() == 0
    assert test_db.query(OperatorStrategy).count() == 0


def _endpoint_field(items, field):
    for item in items:
        if item["field"] == field:
            return item
    raise AssertionError(f"{field} 후보가 응답에 없습니다")
