"""Rule-based notice classification service.

Public surface. The per-axis scoring logic lives in the
``app.services.classification`` package (config/taxonomy/text + region /
eligibility / budget / semantic modules); this file keeps the historical import
surface — ``NoticeClassifierService``, ``RuleAssessment`` and the module-level
``is_construction_project`` / ``detect_regional_preference`` /
``REGION_PREFERENCE_PATTERN`` helpers (imported by ``opportunity_analysis`` and
the tests) — and delegates to that package. Behaviour is unchanged.
"""

import threading

from app.core.config import settings
from app.models.models import CompanyProfile, Project
from app.services.classification import association as association_axis
from app.services.classification import budget as budget_axis
from app.services.classification import config, eligibility, taxonomy
from app.services.classification import region as region_axis
from app.services.classification import semantic as semantic_axis
from app.services.classification import tech_field as tech_field_axis
from app.services.classification import text as text_utils
from app.services.classification.assessment import RuleAssessment
from app.services.classification.region import (  # re-exported public surface
    REGION_PREFERENCE_PATTERN,
    detect_regional_preference,
    is_construction_project,
)

__all__ = [
    "NoticeClassifierService",
    "RuleAssessment",
    "REGION_PREFERENCE_PATTERN",
    "detect_regional_preference",
    "is_construction_project",
]


class NoticeClassifierService:
    """Classify project fit against company profile."""

    # --- Scoring weights & penalties (declarative — classification.config) ---
    MATCH_THRESHOLD = config.MATCH_THRESHOLD
    EXACT_BUSINESS_TYPE_SCORE = config.EXACT_BUSINESS_TYPE_SCORE
    RELATED_BUSINESS_TYPE_SCORE = config.RELATED_BUSINESS_TYPE_SCORE
    LICENSE_MATCH_SCORE = config.LICENSE_MATCH_SCORE
    LICENSE_NEUTRAL_SCORE = config.LICENSE_NEUTRAL_SCORE
    ASSOCIATION_NEUTRAL_SCORE = config.ASSOCIATION_NEUTRAL_SCORE
    ASSOCIATION_MATCH_SCORE = config.ASSOCIATION_MATCH_SCORE
    TECH_FIELD_NEUTRAL_SCORE = config.TECH_FIELD_NEUTRAL_SCORE
    TECH_FIELD_MATCH_SCORE = config.TECH_FIELD_MATCH_SCORE
    REGION_MATCH_SCORE = config.REGION_MATCH_SCORE
    REGION_ADVISORY_SCORE = config.REGION_ADVISORY_SCORE
    REGION_NEUTRAL_SCORE = config.REGION_NEUTRAL_SCORE
    REGION_PREFERENCE_BONUS = config.REGION_PREFERENCE_BONUS
    BUDGET_STRONG_SCORE = config.BUDGET_STRONG_SCORE
    BUDGET_GOOD_SCORE = config.BUDGET_GOOD_SCORE
    BUDGET_BORDERLINE_SCORE = config.BUDGET_BORDERLINE_SCORE
    CAPACITY_HIGH_SCORE = config.CAPACITY_HIGH_SCORE
    CAPACITY_BASE_SCORE = config.CAPACITY_BASE_SCORE
    CAPABILITY_STRONG_SCORE = config.CAPABILITY_STRONG_SCORE
    CAPABILITY_GOOD_SCORE = config.CAPABILITY_GOOD_SCORE
    CAPABILITY_BORDERLINE_SCORE = config.CAPABILITY_BORDERLINE_SCORE
    SEMANTIC_STRONG_SCORE = config.SEMANTIC_STRONG_SCORE
    SEMANTIC_GOOD_SCORE = config.SEMANTIC_GOOD_SCORE
    SEMANTIC_BASE_SCORE = config.SEMANTIC_BASE_SCORE

    BUSINESS_TYPE_MISMATCH_PENALTY = config.BUSINESS_TYPE_MISMATCH_PENALTY
    LICENSE_MISMATCH_PENALTY = config.LICENSE_MISMATCH_PENALTY
    ASSOCIATION_MISMATCH_PENALTY = config.ASSOCIATION_MISMATCH_PENALTY
    TECH_FIELD_MISMATCH_PENALTY = config.TECH_FIELD_MISMATCH_PENALTY
    REGION_MISMATCH_PENALTY = config.REGION_MISMATCH_PENALTY
    BUDGET_MISMATCH_PENALTY = config.BUDGET_MISMATCH_PENALTY
    CAPABILITY_MISMATCH_PENALTY = config.CAPABILITY_MISMATCH_PENALTY
    SEMANTIC_LOW_PENALTY = config.SEMANTIC_LOW_PENALTY
    SEMANTIC_VERY_LOW_PENALTY = config.SEMANTIC_VERY_LOW_PENALTY

    BUDGET_BORDERLINE_RATIO = config.BUDGET_BORDERLINE_RATIO
    AWARD_BONUS_PER_AWARD = config.AWARD_BONUS_PER_AWARD
    AWARD_BONUS_CAP = config.AWARD_BONUS_CAP

    # --- Reference taxonomy (declarative — classification.taxonomy) ----------
    BUSINESS_TYPE_ALIASES = taxonomy.BUSINESS_TYPE_ALIASES
    RELATED_BUSINESS_TYPES = taxonomy.RELATED_BUSINESS_TYPES
    REGION_ALIASES = taxonomy.REGION_ALIASES
    LICENSE_CODE_PATTERN = taxonomy.LICENSE_CODE_PATTERN
    LICENSE_CONTEXT_KEYWORDS = taxonomy.LICENSE_CONTEXT_KEYWORDS
    LICENSE_ALIASES = taxonomy.LICENSE_ALIASES
    REGION_RESTRICTION_KEYWORDS = taxonomy.REGION_RESTRICTION_KEYWORDS
    COMPLEXITY_KEYWORDS = taxonomy.COMPLEXITY_KEYWORDS

    _embedding_model = None
    _embedding_model_name: str | None = None
    _embedding_model_failed = False
    # Serializes the (slow) model load so the startup warm-up thread and a
    # concurrent request cannot each build their own copy.
    _embedding_model_lock = threading.Lock()

    def classify(self, project: Project, profile: CompanyProfile | None) -> dict:
        """Score project fit using business type, region, and budget rules."""
        reasons: list[str] = []

        if not profile:
            reasons.append("업체 프로필이 없어 기본 점수만 반환합니다.")
            return {"matched": False, "score": 0.0, "reasons": reasons}

        axis_assessments = {
            "business_type": self._assess_business_type(project, profile),
            "license": self._assess_license(project, profile),
            "association": self._assess_association(project, profile),
            "tech_field": self._assess_tech_field(project, profile),
            "region": self._assess_region(project, profile),
            "budget": self._assess_budget(project, profile),
            "capability": self._assess_capability(project, profile),
            "semantic_similarity": self._assess_semantic_similarity(project, profile),
        }
        assessments = list(axis_assessments.values())

        for assessment in assessments:
            reasons.extend(assessment.reasons)

        positive_score = sum(assessment.score for assessment in assessments)
        penalties = sum(assessment.penalty for assessment in assessments)
        score = max(0.0, min(1.0, positive_score - penalties))
        matched = all(assessment.passed for assessment in assessments) and score >= self.MATCH_THRESHOLD
        blocking_axes = [axis for axis, assessment in axis_assessments.items() if not assessment.passed]

        if not reasons:
            reasons.append("추가 판별 데이터가 필요합니다.")

        return {
            "matched": matched,
            "score": round(score, 2),
            "reasons": reasons,
            "criteria": self._serialize_assessments(axis_assessments),
            "score_breakdown": {
                "positive_score": round(positive_score, 4),
                "penalty": round(penalties, 4),
                "final_score": round(score, 4),
                "match_threshold": self.MATCH_THRESHOLD,
                "blocking_axes": blocking_axes,
            },
        }

    def _serialize_assessments(self, assessments: dict[str, RuleAssessment]) -> dict[str, dict]:
        """Expose axis-level scoring so operators can see why a candidate was rejected."""
        return {
            axis: {
                "score": round(assessment.score, 4),
                "penalty": round(assessment.penalty, 4),
                "passed": assessment.passed,
                "reasons": assessment.reasons,
            }
            for axis, assessment in assessments.items()
        }

    # --- Per-axis delegators (impl in app.services.classification.*) ---------
    def _assess_business_type(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return eligibility.assess_business_type(project, profile)

    def _assess_license(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return eligibility.assess_license(project, profile)

    def _assess_association(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return association_axis.assess_association(project, profile)

    def _assess_tech_field(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return tech_field_axis.assess_tech_field(project, profile)

    def _assess_region(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return region_axis.assess_region(project, profile)

    def region_preference_boost_applies(
        self, project: Project, profile: CompanyProfile | None
    ) -> bool:
        """Single source of truth for the 지역가산점/지역우대 boost decision.

        Delegates to ``classification.region`` so the classifier boost and the
        opportunity_analysis region_bonus risk-suppression share one definition.
        """
        return region_axis.region_preference_boost_applies(project, profile)

    def _assess_budget(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return budget_axis.assess_budget(project, profile)

    def _assess_capacity_score_budget(self, capacity_score: float) -> RuleAssessment:
        return budget_axis.assess_capacity_score_budget(capacity_score)

    def _assess_capability(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        return budget_axis.assess_capability(project, profile)

    def _assess_semantic_similarity(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        # Pass the (patchable) bound method so tests monkeypatching
        # ``_compute_semantic_similarity`` on the instance are still consulted.
        return semantic_axis.assess_semantic_similarity(
            project, profile, self._compute_semantic_similarity
        )

    def _compute_semantic_similarity(self, project_text: str, profile_text: str) -> tuple[float, str]:
        return semantic_axis.compute_semantic_similarity(
            project_text, profile_text, self._get_embedding_model
        )

    def _describe_project_scope(self, project: Project) -> str:
        return semantic_axis.describe_project_scope(project)

    def _build_project_semantic_text(self, project: Project) -> str:
        return semantic_axis.build_project_semantic_text(project)

    def _normalize_business_type(self, value: str | None) -> str | None:
        return text_utils.normalize_business_type(value)

    def _extract_license_tokens(self, text: str | None, require_context: bool = False) -> set[str]:
        return text_utils.extract_license_tokens(text, require_context)

    def _tokenize_semantic_text(self, text: str) -> list[str]:
        return text_utils.tokenize_semantic_text(text)

    @classmethod
    def get_embedding_model(cls):
        """Public accessor for the shared cached embedding model.

        Same object and cache as ``_get_embedding_model``; exposed so callers
        outside classification (startup warm-up) do not reach into a private
        name. Returns ``None`` when the model cannot be loaded.
        """
        return cls._get_embedding_model()

    @classmethod
    def _cached_embedding_model(cls, model_name: str):
        """Return ``(hit, model)`` for the class-level cache of ``model_name``."""
        if cls._embedding_model is not None and cls._embedding_model_name == model_name:
            return True, cls._embedding_model
        if cls._embedding_model_failed and cls._embedding_model_name == model_name:
            return True, None
        return False, None

    @classmethod
    def _get_embedding_model(cls):
        """Load and cache the sentence-transformers model when available."""
        model_name = settings.CLASSIFIER_EMBEDDING_MODEL
        hit, model = cls._cached_embedding_model(model_name)
        if hit:
            return model

        # The load takes ~25s, so callers arriving during the startup warm-up (or
        # during another request's load) must wait for that one load instead of
        # starting a second copy of the model. Fast path above stays lock-free.
        with cls._embedding_model_lock:
            hit, model = cls._cached_embedding_model(model_name)
            if hit:
                return model

            try:
                from sentence_transformers import SentenceTransformer

                cls._embedding_model = SentenceTransformer(
                    model_name,
                    cache_folder=settings.MODEL_CACHE_DIR,
                    local_files_only=settings.CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY,
                )
                cls._embedding_model_name = model_name
                cls._embedding_model_failed = False
                return cls._embedding_model
            except Exception:
                cls._embedding_model = None
                cls._embedding_model_name = model_name
                cls._embedding_model_failed = True
                return None
