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
    FIELD_ASSOCIATION_MEMBERSHIPS,
    FIELD_BUSINESS_TYPE,
    FIELD_FOCUS_CATEGORIES,
    FIELD_FOCUS_REGIONS,
    FIELD_LICENSE_CODES,
    FIELD_MAX_BUDGET,
    FIELD_MIN_BUDGET,
    FIELD_REGION_CODES,
    FIELD_TECH_FIELDS,
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
    license_names=(),
    region_codes=(),
    budget=None,
    tech_fields=(),
    association_memberships=(),
):
    return NoticeFeatures(
        business_type=business_type,
        category=category,
        license_names=frozenset(license_names),
        region_codes=frozenset(region_codes),
        budget=budget,
        tech_fields=frozenset(tech_fields),
        association_memberships=frozenset(association_memberships),
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
        _feature(business_type="construction", category="construction", license_names=("토목공사업",)),
        _feature(business_type="service", category="service", license_names=("소프트웨어개발업",)),
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


def test_license_names_candidate_respects_min_support():
    """면허명은 최소 지지(2건) 이상만 후보에 포함된다."""
    features = [
        _feature(license_names=("토목공사업",)),
        _feature(license_names=("토목공사업",)),
        _feature(license_names=("항만및해안",)),  # 1건뿐 → 제외
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    suggestion = _by_field(bundle.profile, FIELD_LICENSE_CODES)
    assert suggestion is not None
    assert suggestion.value == ["토목공사업"]
    assert "항만및해안" not in suggestion.value
    assert suggestion.matched_notice_count == 2
    assert "토목공사업(2건)" in suggestion.reason


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
        _feature(license_names=("토목공사업",), region_codes=("부산",), budget=100_000_000),
        _feature(license_names=("토목공사업",), region_codes=("부산",), budget=200_000_000),
        _feature(license_names=("토목공사업",), region_codes=("부산",), budget=300_000_000),
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    all_items = bundle.profile + bundle.strategy
    assert all_items, "후보가 하나 이상 나와야 한다"
    for item in all_items:
        assert item.needs_confirmation is NEEDS_CONFIRMATION is True
        assert 0.0 <= item.confidence < 1.0
        assert item.source == SOURCE_INTERNAL_NOTICES


# --- 순수 집계: cohort 후보(기술부문/협회 가입) ------------------------------


def test_tech_fields_candidate_respects_min_support():
    """요구 기술부문(canonical)이 최소 지지 이상이면 tech_fields 후보로 나온다."""
    features = [
        _feature(tech_fields=("해양엔지니어링",)),
        _feature(tech_fields=("해양엔지니어링",)),
        _feature(tech_fields=("수로조사",)),  # 1건뿐 → 제외
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    suggestion = _by_field(bundle.profile, FIELD_TECH_FIELDS)
    assert suggestion is not None
    assert suggestion.value == ["해양엔지니어링"]
    assert "수로조사" not in suggestion.value
    assert suggestion.source == SOURCE_INTERNAL_NOTICES
    assert suggestion.needs_confirmation is True
    assert suggestion.matched_notice_count == 2
    # 근거 건수가 reason 에 남는다(§2 provenance).
    assert "해양엔지니어링(2건)" in suggestion.reason


def test_association_membership_candidate_from_frequent_requirement():
    """협회 가입 요건이 자주 명시되면 그 협회 canonical 이 후보로 나온다."""
    features = [
        _feature(association_memberships=("엔지니어링협회",)),
        _feature(association_memberships=("엔지니어링협회",)),
        _feature(),  # 요건 없음 → 신호 없음(중립)
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    suggestion = _by_field(bundle.profile, FIELD_ASSOCIATION_MEMBERSHIPS)
    assert suggestion is not None
    assert suggestion.value == ["엔지니어링협회"]
    assert suggestion.matched_notice_count == 2
    assert "엔지니어링협회(2건)" in suggestion.reason


def test_engineering_business_term_does_not_surface_as_association_candidate():
    """FLAG_ENGINEERING_BUSINESS 용어(사업 신고)는 협회 후보로 surface 되지 않는다.

    회귀 가드(리뷰 지적): association_memberships 는 협회 **가입**(FLAG_ASSOCIATION,
    엔지니어링협회)만 제안하고 엔지니어링사업자/활동주체(엔지니어링산업 진흥법상
    사업 신고, FLAG_ENGINEERING_BUSINESS)는 협회 가입이 아니라 제외한다. 자격
    원문에 engineering_business 용어가 MIN_SUPPORTING_NOTICES 이상 있어도 협회
    후보가 나오면 안 된다 — 이 의도적 필터가 미래에 느슨해지지 않도록 고정한다.
    필터가 사는 실제 경로(project_to_features → aggregate_suggestions)를 태운다.
    """
    projects = [
        Project(
            title="해양 기술용역 사업",
            description="",
            requirements="",
            budget_estimate=100_000_000.0,
            category="technical-service",
            eligibility_raw={
                "flags": {},
                "license_limits": [{"lcnsLmtNm": "엔지니어링사업자"}],
            },
        )
        for _ in range(MIN_SUPPORTING_NOTICES)
    ]
    features = [project_to_features(project) for project in projects]

    # 특성 레벨: engineering_business 용어는 association 신호를 세우지 않는다(중립).
    for feature in features:
        assert feature.association_memberships == frozenset()

    bundle = aggregate_suggestions(features, seed=_SEED)

    # 후보 레벨: MIN_SUPPORTING_NOTICES 만큼 있어도 협회 후보로 승격되지 않는다.
    assert _by_field(bundle.profile, FIELD_ASSOCIATION_MEMBERSHIPS) is None


def test_cohort_signals_absent_yield_no_candidates():
    """자격/cohort 데이터가 없는 매칭 공고는 cohort 후보를 내지 않는다(빈, 중립)."""
    features = [
        _feature(business_type="construction"),
        _feature(business_type="construction"),
    ]
    bundle = aggregate_suggestions(features, seed=_SEED)

    assert _by_field(bundle.profile, FIELD_TECH_FIELDS) is None
    assert _by_field(bundle.profile, FIELD_ASSOCIATION_MEMBERSHIPS) is None


# --- 특성 추출(project_to_features) ------------------------------------------


def test_project_to_features_extracts_cohort_signals_from_eligibility_raw():
    """eligibility_raw.license_limits 에서 기술부문/협회 가입 신호를 룰로 추출한다."""
    project = Project(
        title="해양 항만 기술용역",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="technical-service",
        eligibility_raw={
            "flags": {},
            "license_limits": [
                {"lcnsLmtNm": "엔지니어링사업(해양)"},
                {"lcnsLmtNm": "한국엔지니어링협회 회원"},
            ],
        },
    )
    features = project_to_features(project)

    # 해양 면허 → 기술부문 canonical(해양엔지니어링), 협회 원문 → 협회 canonical.
    assert "해양엔지니어링" in features.tech_fields
    assert "엔지니어링협회" in features.association_memberships


def test_project_to_features_no_eligibility_yields_empty_cohort_signals():
    """자격 원문이 없으면 cohort 신호는 빈 집합(중립) — 아무 것도 배제/제안하지 않는다."""
    project = Project(
        title="항만 준설 공사",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        eligibility_raw=None,
    )
    features = project_to_features(project)

    assert features.tech_fields == frozenset()
    assert features.association_memberships == frozenset()


def test_project_to_features_extracts_license_names_from_eligibility_raw():
    """eligibility_raw.license_limits 의 lcnsLmtNm 에서 면허 표시명을 추출한다."""
    project = Project(
        title="항만 준설 공사",
        description="",
        requirements="",
        budget_estimate=500_000_000.0,
        category="construction",
        eligibility_raw={
            "flags": {},
            "license_limits": [{"lcnsLmtNm": "토목공사업/1234", "lmtGrpNo": "1"}],
        },
    )
    features = project_to_features(project)

    # 코드 접미("/1234")가 떨어진 표시명이 나온다 — canonical 코드(CIV001) 아님.
    assert "토목공사업" in features.license_names
    assert "CIV001" not in features.license_names
    assert features.business_type == "construction"
    assert features.budget == 500_000_000.0


def test_license_names_preserve_marine_precision_no_eng001_collapse():
    """실 corpus 형식 면허명은 전문분야를 구분해 내고 generic ENG001 로 붕괴하지 않는다.

    회귀 가드(리뷰 지적): classifier extract_license_tokens 를 원문에 돌리면
    ENG001 포괄 별칭이 해양/항만/전기설비를 한 코드로 collapse 시킨다. 면허 표시명
    네임스페이스는 이 정밀 신호를 보존해야 한다(첫 실수요 고객=해양엔지니어링협회).
    """
    project = Project(
        title="해양 항만 기술용역",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="technical-service",
        eligibility_raw={
            "flags": {},
            "license_limits": [
                {"lcnsLmtNm": "엔지니어링사업(해양)", "lmtGrpNo": "1"},
                {"lcnsLmtNm": "엔지니어링사업(항만, 해안)", "lmtGrpNo": "2"},
                {"lcnsLmtNm": "해양조사정보업(수로측량업)/5034", "lmtGrpNo": "3"},
                {"lcnsLmtNm": "학술.연구용역/1169", "lmtGrpNo": "4"},
            ],
        },
    )
    names = project_to_features(project).license_names

    # 각 전문분야가 구분된 정밀 면허명으로 나온다(코드 접미 제거).
    assert "엔지니어링사업(해양)" in names
    assert "엔지니어링사업(항만, 해안)" in names
    assert "해양조사정보업(수로측량업)" in names  # "/5034" 접미 제거
    assert "학술.연구용역" in names  # "/1169" 접미 제거
    # generic ENG001 하나로 뭉개지지 않는다.
    assert "ENG001" not in names
    # 서로 다른 전문분야가 별개 후보로 남는다(4개 distinct).
    assert len(names) == 4


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

    # 면허 후보는 2건 지지된 "토목공사업"(표시명) 만(1건짜리 "항만및해안" 제외).
    license_item = _endpoint_field(payload["profile"], FIELD_LICENSE_CODES)
    assert license_item["value"] == ["토목공사업"]


def test_endpoint_returns_cohort_candidates(client, test_db):
    """자격 원문에 기술부문/협회 가입이 자주 요구되면 cohort 후보가 나온다."""
    cohort_eligibility = {
        "flags": {},
        "license_limits": [
            {"lcnsLmtNm": "엔지니어링사업(해양)"},
            {"lcnsLmtNm": "한국엔지니어링협회 회원사"},
        ],
    }
    _make_notice(
        test_db,
        title="항만 해양 기술용역 1",
        category="technical-service",
        eligibility_raw=cohort_eligibility,
    )
    _make_notice(
        test_db,
        title="항만 해양 기술용역 2",
        category="technical-service",
        eligibility_raw=cohort_eligibility,
    )

    response = client.get(
        "/api/v1/operator/onboarding-suggestions", params={"keywords": "항만"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    tech = _endpoint_field(payload["profile"], FIELD_TECH_FIELDS)
    assert tech["value"] == ["해양엔지니어링"]
    assert tech["needs_confirmation"] is True
    association = _endpoint_field(payload["profile"], FIELD_ASSOCIATION_MEMBERSHIPS)
    assert association["value"] == ["엔지니어링협회"]
    assert association["needs_confirmation"] is True


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


def test_endpoint_rejects_cross_operator_id(client, test_db):
    """인증 없는 호출이 canonical 이 아닌 operator_id 를 요구하면 403(envelope 계약)."""
    _seed_marine_notices(test_db)

    response = client.get(
        "/api/v1/operator/onboarding-suggestions",
        params={"keywords": "항만", "operator_id": 999999},
    )
    assert response.status_code == 403


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
