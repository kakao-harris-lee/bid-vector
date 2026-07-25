"""Semantic-similarity scoring axis and its text builders.

Blends an embedding (or lexical fallback) similarity between the project text
and the company capability summary into the fit score. Extracted verbatim from
``NoticeClassifierService``.

The embedding-model getter stays on the service class (it caches on class
attributes and is a monkeypatch surface). ``assess_semantic_similarity`` and
``compute_semantic_similarity`` therefore receive the getter / the bound
``_compute_semantic_similarity`` as a callable so the existing patch points
(instance-level ``_compute_semantic_similarity`` and class-level
``_get_embedding_model``) keep working unchanged.
"""

from typing import Callable

from app.core.bands import resolve_band
from app.core.config import settings
from app.models.models import CompanyProfile, Project
from app.services.classification import config, taxonomy
from app.services.classification.assessment import RuleAssessment
from app.services.classification.budget import (
    estimate_company_capability,
    estimate_required_capability,
)
from app.services.classification.text import (
    collect_project_text,
    extract_license_tokens,
    extract_project_regions,
    extract_regions,
    has_enough_semantic_context,
    normalize_business_type,
    tokenize_semantic_text,
)


def assess_semantic_similarity(
    project: Project,
    profile: CompanyProfile,
    compute_similarity: Callable[[str, str], tuple[float, str]],
) -> RuleAssessment:
    """Blend semantic similarity between project text and company capability summary.

    ``compute_similarity`` is the service's bound ``_compute_semantic_similarity``
    so tests can monkeypatch that instance method and still have it consulted.
    """
    if not settings.ENABLE_SEMANTIC_CLASSIFICATION:
        return RuleAssessment(
            score=0.0,
            passed=True,
            reasons=["의미 기반 분류가 비활성화되어 규칙 기반 점수만 사용했습니다."],
        )

    project_text = build_project_semantic_text(project)
    profile_text = build_profile_semantic_text(profile)
    similarity, source = compute_similarity(project_text, profile_text)
    source_label = "임베딩 모델" if source == "sentence-transformers" else "fallback 유사도"
    threshold = settings.CLASSIFIER_SEMANTIC_MATCH_THRESHOLD
    has_semantic_context = has_enough_semantic_context(project_text, profile_text)

    if similarity >= threshold + settings.CLASSIFIER_SEMANTIC_STRONG_MARGIN:
        return RuleAssessment(
            score=config.SEMANTIC_STRONG_SCORE,
            passed=True,
            reasons=[f"공고와 업체 역량 요약의 의미 유사도가 매우 높습니다. ({source_label}: {similarity:.2f})"],
        )

    if similarity >= threshold:
        return RuleAssessment(
            score=config.SEMANTIC_GOOD_SCORE,
            passed=True,
            reasons=[f"공고 내용과 업체 역량의 의미 유사도가 양호합니다. ({source_label}: {similarity:.2f})"],
        )

    if similarity >= max(0.0, threshold - settings.CLASSIFIER_SEMANTIC_BASE_MARGIN):
        return RuleAssessment(
            score=config.SEMANTIC_BASE_SCORE,
            passed=True,
            reasons=[f"의미 유사도가 보통 수준으로 확인되어 보수적 가점을 반영했습니다. ({source_label}: {similarity:.2f})"],
        )

    if similarity <= settings.CLASSIFIER_SEMANTIC_VERY_LOW_MAX:
        return RuleAssessment(
            score=0.0,
            passed=not has_semantic_context,
            penalty=config.SEMANTIC_VERY_LOW_PENALTY,
            reasons=[f"의미 유사도가 매우 낮아 규칙 기반 점수의 false positive 가능성을 줄이기 위해 {'차단 신호와 ' if has_semantic_context else ''}큰 감점을 반영했습니다. ({source_label}: {similarity:.2f})"],
        )

    if similarity <= settings.CLASSIFIER_SEMANTIC_LOW_MAX:
        return RuleAssessment(
            score=0.0,
            passed=True,
            penalty=config.SEMANTIC_LOW_PENALTY,
            reasons=[f"의미 유사도가 낮아 최종 추천 점수를 보수적으로 낮췄습니다. ({source_label}: {similarity:.2f})"],
        )

    return RuleAssessment(
        score=0.0,
        passed=True,
        reasons=[f"의미 유사도는 중간 이하 수준이어서 추가 가점 없이 유지했습니다. ({source_label}: {similarity:.2f})"],
    )


def build_project_semantic_text(project: Project) -> str:
    """Build a semantic text summary for the project."""
    project_type = normalize_business_type(project.category)
    project_regions, _ = extract_project_regions(project)
    project_licenses = extract_license_tokens(collect_project_text(project), require_context=False)
    parts = [collect_project_text(project)]

    if project.category:
        parts.append(f"공고업종 {project.category}")
    if project_type:
        parts.append("업종동의어 " + " ".join(sorted(taxonomy.BUSINESS_TYPE_ALIASES.get(project_type, {project_type}))))
    if project_regions:
        parts.append("지역요건 " + " ".join(sorted(project_regions)))
    if project_licenses:
        parts.append("필수면허 " + " ".join(sorted(project_licenses)))
    parts.append(describe_project_scope(project))

    return " ".join(part for part in parts if part)


def build_profile_semantic_text(profile: CompanyProfile) -> str:
    """Build a semantic text summary for the company profile."""
    profile_type = normalize_business_type(profile.business_type)
    profile_licenses = extract_license_tokens(profile.license_codes)
    profile_regions = extract_regions(profile.region_codes)
    parts = [f"업체업종 {profile.business_type}"]

    if profile_type:
        parts.append("업종동의어 " + " ".join(sorted(taxonomy.BUSINESS_TYPE_ALIASES.get(profile_type, {profile_type}))))
    if profile_licenses:
        parts.append("보유면허 " + " ".join(sorted(profile_licenses)))
    if profile_regions:
        parts.append("수행지역 " + " ".join(sorted(profile_regions)))
    parts.append(describe_company_capability(profile))

    return " ".join(part for part in parts if part)


def describe_project_scope(project: Project) -> str:
    """Describe project scale and complexity in text form for semantic matching."""
    required_capability = estimate_required_capability(project)
    scale = resolve_band(required_capability, config.PROJECT_SCOPE_BANDS)
    return f"{scale} 요구역량 {required_capability:.2f}"


def describe_company_capability(profile: CompanyProfile) -> str:
    """Describe company capability as text for semantic matching."""
    available_capability = estimate_company_capability(profile)
    annual_revenue = float(profile.annual_revenue or 0.0)
    awards = int(profile.total_awards or 0)

    revenue_band = next(
        label for predicate, label in config.REVENUE_CAPABILITY_BANDS if predicate(annual_revenue)
    )
    award_band = resolve_band(awards, config.AWARD_CAPABILITY_BANDS)

    return f"업체역량 {available_capability:.2f} {revenue_band} {award_band}"


def compute_semantic_similarity(
    project_text: str,
    profile_text: str,
    get_embedding_model: Callable[[], object],
) -> tuple[float, str]:
    """Compute semantic similarity using sentence-transformers with a lexical fallback.

    ``get_embedding_model`` is the service's ``_get_embedding_model`` (class-level
    cached loader / monkeypatch surface); passing it keeps the model caching and
    the ml-release patch point on the class.
    """
    if not project_text.strip() or not profile_text.strip():
        return 0.0, "fallback-empty"

    if settings.ENVIRONMENT != "test":
        model = get_embedding_model()
        if model is not None:
            embeddings = model.encode([project_text, profile_text], normalize_embeddings=True)
            similarity = float(embeddings[0] @ embeddings[1])
            return max(0.0, min(1.0, similarity)), "sentence-transformers"

    return fallback_semantic_similarity(project_text, profile_text), "fallback-lexical"


def fallback_semantic_similarity(project_text: str, profile_text: str) -> float:
    """Fallback lexical similarity used in tests or offline environments."""
    project_tokens = tokenize_semantic_text(project_text)
    profile_tokens = tokenize_semantic_text(profile_text)

    if not project_tokens or not profile_tokens:
        return 0.0

    project_token_set = set(project_tokens)
    profile_token_set = set(profile_tokens)
    overlap = project_token_set & profile_token_set
    similarity = (2 * len(overlap)) / (len(project_token_set) + len(profile_token_set))
    return round(max(0.0, min(1.0, similarity)), 4)
