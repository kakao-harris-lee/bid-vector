"""내부 공고에서 프로필/전략 필드를 역추천하는 온보딩 후보 생성기(읽기 전용).

설계 근거: ``docs/superpowers/specs/
2026-07-03-business-number-guided-onboarding-design.md`` §2 "KONEPS/내부 공고
후보". seed(키워드/지역/예산 힌트)에 매칭되는 기존 ``Project`` 공고를 모아
업무구분·면허제한·지역제한·금액대에서 회사 프로필/감시 전략 후보를 집계한다.

정직 명세(§2)·설계 §2 규칙:
- 후보는 **추정**이며 확정이 아니다. 이 모듈은 후보를 어디에도 persist 하지 않고
  ``CompanyProfile``/``OperatorStrategy`` 를 변경하지 않는다. 반환만 한다.
- 모든 후보에 ``source``/``confidence``/``needs_confirmation``/``reason``/
  ``matched_notice_count`` 를 붙인다. ``needs_confirmation`` 은 항상 ``True`` —
  사용자 확정 전에는 추천 엔진에 반영하지 않는다.

설계 규율:
- **I/O 와 계산 분리**(§4.7.4): DB 조회(:func:`load_notice_features`)는 순수
  :class:`NoticeFeatures` 리스트만 만들고, 후보 집계
  (:func:`aggregate_suggestions`)는 그 리스트를 받는 **순수 함수**다. 값 테이블
  테스트는 :func:`aggregate_suggestions` 를 진입점으로 쓴다.
- **매직값 금지**(§4.5.1): 임계·confidence 밴드·top-N·백분위는 모듈 상단 상수
  테이블로 선언한다. 이 모듈 전용이라 config Settings 로 승격하지 않는다.
- **패턴 재사용**(§4.6): 면허명 추출은 ``license_eligibility.
  license_limit_names`` (corpus-anchored 파서·코드 접미 제거 재사용)를, 지역/
  업무구분 정규화는 ``classification.text`` 헬퍼를 재사용한다(복붙 금지).

license_codes 후보의 네임스페이스(면허명, 코드 아님) 근거: ``CompanyProfile.
license_codes`` 는 한글 면허명을 저장하고(front chip·canonical operator 값
["엔지니어링","항만및해안",...]), 두 소비자(classifier ``assess_license`` 와
``license_eligibility.assess_license_eligibility``)가 그 값을 재토큰화해
비교한다. classifier 의 ``extract_license_tokens`` 를 원문 면허명에 돌리면
ENG001 포괄 별칭이 해양/항만/전기설비 전문분야를 한 코드로 collapse 시키므로
(``엔지니어링사업(해양)`` → ENG001), 정밀 신호가 소실된다. 반대로 면허 **표시명**
은 전문분야를 구분해 보존하고 두 소비자 경로(원문 정규화 키 비교)에 정합한다.
따라서 후보값은 canonical 코드가 아니라 면허 표시명이다.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional, Union

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import Project
from app.services.classification.text import (
    extract_project_regions,
    normalize_business_type,
)
from app.services.license_eligibility import license_limit_names

# --- 후보 소스/필드 이름 (단일 출처, §4.5.1) --------------------------------

# 이 슬라이스가 생성하는 후보의 유일한 소스. 설계 §2 의 "KONEPS/내부 공고 후보".
SOURCE_INTERNAL_NOTICES = "internal_notices"

# 후보가 채우는 대상 필드명. 설계 §3 확정 대상 필드(CompanyProfile /
# OperatorStrategy 컬럼명)와 정확히 일치시킨다 — 후속 apply PR 이 이 이름으로
# 부분 반영하기 때문이다.
FIELD_BUSINESS_TYPE = "business_type"
FIELD_LICENSE_CODES = "license_codes"
FIELD_REGION_CODES = "region_codes"
FIELD_FOCUS_CATEGORIES = "focus_categories"
FIELD_FOCUS_REGIONS = "focus_regions"
FIELD_MIN_BUDGET = "min_budget_estimate"
FIELD_MAX_BUDGET = "max_budget_estimate"

# 후보는 확정이 아니므로 항상 사용자 확인이 필요하다(설계 §2, 정직 명세 §2).
NEEDS_CONFIRMATION = True

# --- 집계 임계·밴드 (매직값 금지, §4.5.1) -----------------------------------

# 후보 하나를 낼 최소 지지 공고 수. 단발 공고(우연 매칭)를 후보로 승격하지 않는다.
MIN_SUPPORTING_NOTICES = 2

# 예산 후보(min/max)를 낼 최소 예산 확인 공고 수. 분포를 논할 최소 표본.
MIN_BUDGET_SAMPLES = 3

# 리스트 필드(면허/지역/카테고리) 후보의 최대 개수(top-N). 상위 신호만 남긴다.
MAX_CANDIDATES_PER_FIELD = 5

# confidence 밴드: (지지비율 하한, confidence). 지지비율 = 최다 신호 지지 공고 수
# / 매칭 공고 수. 내림차순 평가하는 **선언 룩업 테이블**(§4.5.2 — if-else 트리
# 금지). 상한 0.8: 사용자 미확정 후보에 1.0(완전 확신)을 부여하지 않는다(§2 정직).
CONFIDENCE_BANDS: tuple[tuple[float, float], ...] = (
    (0.60, 0.80),
    (0.35, 0.60),
    (0.15, 0.40),
    (0.00, 0.25),
)

# 예산 분포 하/상한 백분위(선형보간). 극단 이상치를 밴드에서 배제한다.
BUDGET_LOW_PERCENTILE = 0.10
BUDGET_HIGH_PERCENTILE = 0.90

# 예산 후보 반올림 단위(원). 하한은 내림, 상한은 올림해 정수 만원 단위 envelope 로.
BUDGET_ROUNDING_UNIT = 10_000

# DB 에서 끌어올 최대 공고 수(집계 안정성·비용 상한). 최근 등록순으로 자른다.
MAX_NOTICES_SCANNED = 500

# 후보 없음 진단(설계 §4: 데이터 부족 vs 과필터링 구분의 최소 형태).
_DIAGNOSTIC_NO_MATCH = (
    "seed 키워드에 매칭되는 내부 공고가 없습니다 — 키워드가 너무 좁거나 해당 "
    "업종 공고 데이터가 아직 부족합니다."
)
_DIAGNOSTIC_MATCH_NO_CANDIDATE = (
    "{count}개 공고가 매칭됐으나 최소 지지({min})를 넘는 후보가 없습니다 — "
    "자격/지역/예산 원문이 희소하거나 키워드를 넓힐 필요가 있습니다."
)
_DIAGNOSTIC_OK = "{count}개 공고에서 프로필 후보 {profile}개·전략 후보 {strategy}개를 도출했습니다."


# --- seed / 순수 특성 / 후보 스키마 ------------------------------------------


@dataclass(frozen=True)
class OnboardingSeed:
    """온보딩 후보 생성 입력. 키워드는 필수, 지역/예산 힌트는 선택."""

    keywords: tuple[str, ...]
    region: Optional[str] = None
    min_budget: Optional[float] = None
    max_budget: Optional[float] = None

    @classmethod
    def from_inputs(
        cls,
        keywords: Sequence[str] | None,
        *,
        region: Optional[str] = None,
        min_budget: Optional[float] = None,
        max_budget: Optional[float] = None,
    ) -> "OnboardingSeed":
        """외부 입력을 정규화한 seed 를 만든다(공백 제거·중복 제거·빈값 배제)."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in keywords or ():
            term = (raw or "").strip()
            if term and term not in seen:
                seen.add(term)
                cleaned.append(term)
        region_hint = (region or "").strip() or None
        return cls(
            keywords=tuple(cleaned),
            region=region_hint,
            min_budget=min_budget,
            max_budget=max_budget,
        )


@dataclass(frozen=True)
class NoticeFeatures:
    """한 공고에서 뽑은 후보 신호(순수 경계 — ORM 비의존).

    :func:`aggregate_suggestions` 는 이 리스트만 받으므로 값 테이블 테스트가
    DB 없이 가능하다.
    """

    business_type: Optional[str]  # normalize_business_type(category) — 정규화 업무구분
    category: Optional[str]  # 원본 project.category(소문자) — focus_categories 소스
    license_names: frozenset[str]  # license_limits 면허 표시명(코드 접미 제거, 전문분야 구분)
    region_codes: frozenset[str]  # 지역제한이 명시된 공고의 지역만
    budget: Optional[float]  # budget_estimate(또는 max/min 폴백), 양수만


@dataclass(frozen=True)
class FieldSuggestion:
    """단일 필드 후보 + provenance(설계 §2: source/confidence/needs_confirmation/reason)."""

    field: str
    value: Union[str, float, list[str]]
    source: str
    confidence: float
    needs_confirmation: bool
    reason: str
    matched_notice_count: int

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "needs_confirmation": self.needs_confirmation,
            "reason": self.reason,
            "matched_notice_count": self.matched_notice_count,
        }


@dataclass(frozen=True)
class SuggestionBundle:
    """프로필/전략 후보 묶음 + 매칭 요약(설계 §4 후보 없음 원인 진단 포함)."""

    profile: list[FieldSuggestion]
    strategy: list[FieldSuggestion]
    matched_notice_count: int
    diagnostics: str


# --- 순수 헬퍼 (계산만, IO 없음) ---------------------------------------------


def _confidence_for_ratio(ratio: float) -> float:
    """지지비율을 confidence 밴드로 매핑한다(선언 룩업, §4.5.2)."""
    for lower, confidence in CONFIDENCE_BANDS:
        if ratio >= lower:
            return confidence
    return CONFIDENCE_BANDS[-1][1]


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """정렬된 값 리스트의 백분위(선형보간). 빈 리스트는 0.0."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = fraction * (len(sorted_values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    weight = rank - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _floor_to_unit(value: float) -> float:
    return math.floor(value / BUDGET_ROUNDING_UNIT) * BUDGET_ROUNDING_UNIT


def _ceil_to_unit(value: float) -> float:
    return math.ceil(value / BUDGET_ROUNDING_UNIT) * BUDGET_ROUNDING_UNIT


def _multi_value_suggestion(
    field: str,
    sets: list[frozenset[str]],
    total: int,
) -> Optional[FieldSuggestion]:
    """다중값 필드(면허/지역/카테고리) 후보 하나를 만든다(순수).

    공고별 값 집합을 세어 ``MIN_SUPPORTING_NOTICES`` 이상 지지받은 값을 상위
    ``MAX_CANDIDATES_PER_FIELD`` 개까지 후보 리스트로 낸다. confidence 는 최다
    신호의 지지비율로, matched_notice_count 는 후보 값 중 하나라도 가진 공고 수로.
    """
    counter: Counter[str] = Counter()
    for value_set in sets:
        for value in value_set:
            counter[value] += 1

    qualifying = [
        (value, count)
        for value, count in counter.most_common()
        if count >= MIN_SUPPORTING_NOTICES
    ][:MAX_CANDIDATES_PER_FIELD]
    if not qualifying:
        return None

    values = [value for value, _ in qualifying]
    value_set = set(values)
    supporting = sum(1 for candidate in sets if candidate & value_set)
    top_ratio = qualifying[0][1] / total if total else 0.0
    detail = ", ".join(f"{value}({count}건)" for value, count in qualifying)
    reason = f"매칭된 {total}개 공고 중 {supporting}개에서 도출: {detail}"
    return FieldSuggestion(
        field=field,
        value=values,
        source=SOURCE_INTERNAL_NOTICES,
        confidence=_confidence_for_ratio(top_ratio),
        needs_confirmation=NEEDS_CONFIRMATION,
        reason=reason,
        matched_notice_count=supporting,
    )


def _dominant_suggestion(
    field: str,
    values: list[Optional[str]],
    total: int,
) -> Optional[FieldSuggestion]:
    """단일값 필드(업무구분) 후보 — 최다 분포 값 하나(순수)."""
    counter: Counter[str] = Counter(value for value in values if value)
    if not counter:
        return None
    value, count = counter.most_common(1)[0]
    if count < MIN_SUPPORTING_NOTICES:
        return None
    ratio = count / total if total else 0.0
    reason = f"매칭된 {total}개 공고 중 {count}개가 '{value}' 업무구분으로 최다 분포입니다."
    return FieldSuggestion(
        field=field,
        value=value,
        source=SOURCE_INTERNAL_NOTICES,
        confidence=_confidence_for_ratio(ratio),
        needs_confirmation=NEEDS_CONFIRMATION,
        reason=reason,
        matched_notice_count=count,
    )


def _budget_suggestions(
    budgets: list[Optional[float]],
    total: int,
) -> list[FieldSuggestion]:
    """예산 분포에서 min/max 후보 두 개를 만든다(순수).

    ``MIN_BUDGET_SAMPLES`` 미만이면 후보 없음. 하한은 저백분위를 내림, 상한은
    고백분위를 올림해 만원 단위 envelope 로 낸다.
    """
    valid = sorted(value for value in budgets if value is not None and value > 0)
    if len(valid) < MIN_BUDGET_SAMPLES:
        return []

    low = _floor_to_unit(_percentile(valid, BUDGET_LOW_PERCENTILE))
    high = _ceil_to_unit(_percentile(valid, BUDGET_HIGH_PERCENTILE))
    sample = len(valid)
    confidence = _confidence_for_ratio(sample / total if total else 0.0)
    low_pct = int(BUDGET_LOW_PERCENTILE * 100)
    high_pct = int(BUDGET_HIGH_PERCENTILE * 100)
    return [
        FieldSuggestion(
            field=FIELD_MIN_BUDGET,
            value=low,
            source=SOURCE_INTERNAL_NOTICES,
            confidence=confidence,
            needs_confirmation=NEEDS_CONFIRMATION,
            reason=(
                f"매칭된 {total}개 공고 중 예산 확인 {sample}개의 {low_pct} 백분위 하한입니다."
            ),
            matched_notice_count=sample,
        ),
        FieldSuggestion(
            field=FIELD_MAX_BUDGET,
            value=high,
            source=SOURCE_INTERNAL_NOTICES,
            confidence=confidence,
            needs_confirmation=NEEDS_CONFIRMATION,
            reason=(
                f"매칭된 {total}개 공고 중 예산 확인 {sample}개의 {high_pct} 백분위 상한입니다."
            ),
            matched_notice_count=sample,
        ),
    ]


def _summarize_diagnostics(
    total: int,
    profile: list[FieldSuggestion],
    strategy: list[FieldSuggestion],
) -> str:
    """후보 유무/원인을 한국어로 요약한다(설계 §4 최소 형태)."""
    if total == 0:
        return _DIAGNOSTIC_NO_MATCH
    if not profile and not strategy:
        return _DIAGNOSTIC_MATCH_NO_CANDIDATE.format(
            count=total, min=MIN_SUPPORTING_NOTICES
        )
    return _DIAGNOSTIC_OK.format(
        count=total, profile=len(profile), strategy=len(strategy)
    )


# --- 순수 집계 (테스트 진입점) -----------------------------------------------


def aggregate_suggestions(
    features: Sequence[NoticeFeatures],
    *,
    seed: OnboardingSeed,
) -> SuggestionBundle:
    """매칭 공고 특성 리스트에서 프로필/전략 후보를 집계한다(**순수 함수**).

    IO/DB 접근 없음 — :class:`NoticeFeatures` 리스트만 받는다. 필드별 집계는
    선언 헬퍼로 위임하며, 임계 미달 필드는 후보를 내지 않는다(빈 리스트 허용).
    ``seed`` 는 후속 진단/필터 확장 지점으로 시그니처에 유지한다.
    """
    total = len(features)
    if total == 0:
        return SuggestionBundle(
            profile=[],
            strategy=[],
            matched_notice_count=0,
            diagnostics=_DIAGNOSTIC_NO_MATCH,
        )

    region_sets = [feature.region_codes for feature in features]
    category_sets = [
        frozenset({feature.category}) if feature.category else frozenset()
        for feature in features
    ]

    profile_builders: list[Optional[FieldSuggestion]] = [
        _dominant_suggestion(
            FIELD_BUSINESS_TYPE, [feature.business_type for feature in features], total
        ),
        _multi_value_suggestion(
            FIELD_LICENSE_CODES, [feature.license_names for feature in features], total
        ),
        _multi_value_suggestion(FIELD_REGION_CODES, region_sets, total),
    ]

    strategy_builders: list[Optional[FieldSuggestion]] = [
        _multi_value_suggestion(FIELD_FOCUS_CATEGORIES, category_sets, total),
        _multi_value_suggestion(FIELD_FOCUS_REGIONS, region_sets, total),
    ]

    profile = [item for item in profile_builders if item is not None]
    strategy = [item for item in strategy_builders if item is not None]
    strategy.extend(_budget_suggestions([feature.budget for feature in features], total))

    return SuggestionBundle(
        profile=profile,
        strategy=strategy,
        matched_notice_count=total,
        diagnostics=_summarize_diagnostics(total, profile, strategy),
    )


# --- I/O 경계 (얇은 조회 → 순수 특성) ----------------------------------------


def _extract_license_names(eligibility_raw: object) -> frozenset[str]:
    """공고 자격 원문의 ``license_limits`` 에서 면허 **표시명**을 추출한다.

    설계 §2: ``eligibility_raw.license_limits`` 의 ``lcnsLmtNm``(면허명/코드)에서
    코드 접미("/1253")를 뗀 표시명을 후보로 낸다. canonical 코드가 아니라
    표시명인 이유는 모듈 docstring 참조(ENG001/HYDRO001 collapse 회피 + 프로필
    저장 값 공간 정합). ``license_eligibility.license_limit_names`` 의
    corpus-anchored 파서를 재사용한다(복붙 금지, §4.6).
    """
    if not isinstance(eligibility_raw, dict):
        return frozenset()
    return frozenset(license_limit_names(eligibility_raw))


def _project_budget(project: Project) -> Optional[float]:
    """공고의 대표 예산액(원). budget_estimate 우선, 없으면 max/min 폴백."""
    for value in (project.budget_estimate, project.budget_max, project.budget_min):
        if value is not None and float(value) > 0:
            return float(value)
    return None


def project_to_features(project: Project) -> NoticeFeatures:
    """``Project`` 한 건을 순수 :class:`NoticeFeatures` 로 변환한다.

    지역은 **지역제한이 명시된 공고**의 지역만 반영한다(설계 §2 "지역제한"):
    ``extract_project_regions`` 의 strict-limit 신호가 없으면 지역 신호를 비운다.
    """
    project_regions, has_strict_limit = extract_project_regions(project)
    region_codes = frozenset(project_regions) if has_strict_limit else frozenset()
    category = (project.category or "").strip().lower() or None
    return NoticeFeatures(
        business_type=normalize_business_type(project.category),
        category=category,
        license_names=_extract_license_names(project.eligibility_raw),
        region_codes=region_codes,
        budget=_project_budget(project),
    )


def _keyword_filter(seed: OnboardingSeed) -> Optional[object]:
    """seed 키워드를 title/description/requirements OR ilike 절로 만든다."""
    text_columns = (Project.title, Project.description, Project.requirements)
    clauses = []
    for keyword in seed.keywords:
        like = f"%{keyword}%"
        for column in text_columns:
            clauses.append(column.ilike(like))
    if not clauses:
        return None
    return or_(*clauses)


def _region_filter(seed: OnboardingSeed) -> Optional[object]:
    """지역 힌트를 공고 텍스트/기관명 OR ilike 절로 만든다(선택)."""
    if not seed.region:
        return None
    like = f"%{seed.region}%"
    return or_(
        Project.title.ilike(like),
        Project.description.ilike(like),
        Project.requirements.ilike(like),
        Project.issuing_agency.ilike(like),
        Project.demand_agency.ilike(like),
    )


def load_notice_features(
    db: Session,
    *,
    seed: OnboardingSeed,
    max_notices: int = MAX_NOTICES_SCANNED,
) -> list[NoticeFeatures]:
    """seed 에 매칭되는 공고를 조회해 순수 :class:`NoticeFeatures` 로 변환한다.

    유일한 IO 경계(§4.7.4). 키워드가 없으면 전체 스캔을 막기 위해 빈 리스트를
    돌려준다(라우터도 422 로 거른다). 예산 힌트는 조회 필터로 매칭 집합을 좁힌다.
    """
    if not seed.keywords:
        return []

    query = db.query(Project)
    keyword_clause = _keyword_filter(seed)
    if keyword_clause is not None:
        query = query.filter(keyword_clause)
    region_clause = _region_filter(seed)
    if region_clause is not None:
        query = query.filter(region_clause)
    if seed.min_budget is not None:
        query = query.filter(Project.budget_estimate >= seed.min_budget)
    if seed.max_budget is not None:
        query = query.filter(Project.budget_estimate <= seed.max_budget)

    projects = (
        query.order_by(Project.created_at.desc()).limit(max_notices).all()
    )
    return [project_to_features(project) for project in projects]


def suggest_onboarding_fields(
    db: Session,
    *,
    seed: OnboardingSeed,
) -> SuggestionBundle:
    """온보딩 후보 생성 진입점 — 조회(IO) → 집계(순수)로 위임한다.

    후보를 어디에도 persist 하지 않고 반환만 한다(정직 명세 §2). operator 데이터를
    읽거나 쓰지 않으므로 canonical/synthetic 오염 위험이 없다(공용 공고만 조회).
    """
    features = load_notice_features(db, seed=seed)
    return aggregate_suggestions(features, seed=seed)


__all__ = [
    "SOURCE_INTERNAL_NOTICES",
    "FIELD_BUSINESS_TYPE",
    "FIELD_LICENSE_CODES",
    "FIELD_REGION_CODES",
    "FIELD_FOCUS_CATEGORIES",
    "FIELD_FOCUS_REGIONS",
    "FIELD_MIN_BUDGET",
    "FIELD_MAX_BUDGET",
    "NEEDS_CONFIRMATION",
    "OnboardingSeed",
    "NoticeFeatures",
    "FieldSuggestion",
    "SuggestionBundle",
    "aggregate_suggestions",
    "project_to_features",
    "load_notice_features",
    "suggest_onboarding_fields",
]
