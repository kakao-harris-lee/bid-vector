"""Rule-based notice classification service."""

import re
from dataclasses import dataclass

from app.core.config import settings
from app.models.models import CompanyProfile, Project


@dataclass
class RuleAssessment:
    """Result of a single rule-based classification check."""

    score: float
    passed: bool
    reasons: list[str]
    penalty: float = 0.0


class NoticeClassifierService:
    """Classify project fit against company profile."""

    MATCH_THRESHOLD = 0.65
    EXACT_BUSINESS_TYPE_SCORE = 0.35
    RELATED_BUSINESS_TYPE_SCORE = 0.2
    LICENSE_MATCH_SCORE = 0.25
    LICENSE_NEUTRAL_SCORE = 0.05
    REGION_MATCH_SCORE = 0.2
    REGION_ADVISORY_SCORE = 0.12
    REGION_NEUTRAL_SCORE = 0.05
    BUDGET_STRONG_SCORE = 0.2
    BUDGET_GOOD_SCORE = 0.15
    BUDGET_BORDERLINE_SCORE = 0.1
    CAPACITY_HIGH_SCORE = 0.12
    CAPACITY_BASE_SCORE = 0.06
    CAPABILITY_STRONG_SCORE = 0.15
    CAPABILITY_GOOD_SCORE = 0.1
    CAPABILITY_BORDERLINE_SCORE = 0.05
    SEMANTIC_STRONG_SCORE = 0.18
    SEMANTIC_GOOD_SCORE = 0.12
    SEMANTIC_BASE_SCORE = 0.05

    BUSINESS_TYPE_MISMATCH_PENALTY = 0.3
    LICENSE_MISMATCH_PENALTY = 0.35
    REGION_MISMATCH_PENALTY = 0.3
    BUDGET_MISMATCH_PENALTY = 0.25
    CAPABILITY_MISMATCH_PENALTY = 0.25
    SEMANTIC_LOW_PENALTY = 0.18
    SEMANTIC_VERY_LOW_PENALTY = 0.3

    BUSINESS_TYPE_ALIASES = {
        "software": {"software", "소프트웨어", "sw", "ict", "정보화"},
        "technical-service": {"technical-service", "기술용역", "엔지니어링", "설계", "감리"},
        "service": {"service", "general-service", "일반용역", "용역"},
        "goods": {"goods", "물품", "구매", "납품"},
        "construction": {"construction", "공사", "건설"},
        "other": {"other", "기타"},
    }
    RELATED_BUSINESS_TYPES = {
        "software": {"technical-service", "service"},
        "technical-service": {"software", "service"},
        "service": {"software", "technical-service"},
        "goods": set(),
        "construction": set(),
        "other": set(),
    }
    REGION_ALIASES = {
        "전국": ("전국",),
        "서울": ("서울", "서울시", "서울특별시"),
        "부산": ("부산", "부산시", "부산광역시"),
        "대구": ("대구", "대구시", "대구광역시"),
        "인천": ("인천", "인천시", "인천광역시"),
        "광주": ("광주", "광주시", "광주광역시"),
        "대전": ("대전", "대전시", "대전광역시"),
        "울산": ("울산", "울산시", "울산광역시"),
        "세종": ("세종", "세종시", "세종특별자치시"),
        "경기": ("경기", "경기도"),
        "강원": ("강원", "강원도", "강원특별자치도"),
        "충북": ("충북", "충청북도"),
        "충남": ("충남", "충청남도"),
        "전북": ("전북", "전라북도", "전북특별자치도"),
        "전남": ("전남", "전라남도"),
        "경북": ("경북", "경상북도"),
        "경남": ("경남", "경상남도"),
        "제주": ("제주", "제주도", "제주특별자치도"),
    }
    LICENSE_CODE_PATTERN = re.compile(r"\b[A-Z]{2,}\d{2,}\b")
    LICENSE_CONTEXT_KEYWORDS = ("면허", "자격", "등록", "보유", "필수", "필요", "요건", "제한")
    LICENSE_ALIASES = {
        "SW001": ("SW001", "소프트웨어사업자", "소프트웨어 사업자", "sw사업자"),
        "NET001": ("NET001", "정보통신공사업", "정보통신공사"),
        "ENG001": ("ENG001", "엔지니어링사업", "엔지니어링", "기술사", "감리"),
        "SEC001": ("SEC001", "정보보호전문서비스", "보안관제", "isms"),
        "ELE001": ("ELE001", "전기공사업", "전기"),
        "FIRE001": ("FIRE001", "소방시설", "소방"),
        # 건설 면허 (front stores the exact "○○공사업" strings; aliases below must
        # contain them verbatim so profile.license_codes ↔ project requirement
        # matching works). Bare "건축"/"토목"/"기계"/"가스"/"조경" are deliberately
        # NOT aliases — they are too generic and would over-match.
        "ARC001": ("ARC001", "건축공사업"),
        "CIV001": ("CIV001", "토목공사업"),
        "CIVARC001": ("CIVARC001", "토목건축공사업"),
        "LND001": ("LND001", "조경공사업"),
        "ENV001": ("ENV001", "산업환경설비공사업", "산업·환경설비공사업", "산업·환경설비"),
        "INT001": ("INT001", "실내건축공사업"),
        "MEC001": ("MEC001", "기계설비공사업"),
        "GAS001": ("GAS001", "가스시설공사업"),
    }
    REGION_RESTRICTION_KEYWORDS = (
        "지역제한",
        "소재 업체",
        "소재업체",
        "소재지",
        "업체만",
        "입찰 가능",
        "참여 가능",
        "관할",
        "등록 업체",
        "제한경쟁",
    )
    COMPLEXITY_KEYWORDS = {
        "통합",
        "고도화",
        "운영",
        "유지관리",
        "24시간",
        "대규모",
        "다기관",
        "클라우드",
        "센터",
    }

    _embedding_model = None
    _embedding_model_name: str | None = None
    _embedding_model_failed = False

    def classify(self, project: Project, profile: CompanyProfile | None) -> dict:
        """Score project fit using business type, region, and budget rules."""
        reasons: list[str] = []

        if not profile:
            reasons.append("업체 프로필이 없어 기본 점수만 반환합니다.")
            return {"matched": False, "score": 0.0, "reasons": reasons}

        axis_assessments = {
            "business_type": self._assess_business_type(project, profile),
            "license": self._assess_license(project, profile),
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

    def _assess_business_type(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        """Evaluate whether the company business type fits the project category."""
        project_type = self._normalize_business_type(project.category)
        profile_type = self._normalize_business_type(profile.business_type)

        if not project_type or not profile_type:
            return RuleAssessment(
                score=0.0,
                passed=False,
                penalty=self.BUSINESS_TYPE_MISMATCH_PENALTY,
                reasons=["업무 구분 정보가 부족해 1차 업종 필터를 통과시키지 않았습니다."],
            )

        if project_type == profile_type:
            return RuleAssessment(
                score=self.EXACT_BUSINESS_TYPE_SCORE,
                passed=True,
                reasons=[f"업무 구분이 일치합니다. (공고: {project.category} / 업체: {profile.business_type})"],
            )

        if profile_type in self.RELATED_BUSINESS_TYPES.get(project_type, set()) or project_type in self.RELATED_BUSINESS_TYPES.get(profile_type, set()):
            return RuleAssessment(
                score=self.RELATED_BUSINESS_TYPE_SCORE,
                passed=True,
                reasons=[f"업무 구분이 인접 분야로 확인되어 부분 적합으로 반영했습니다. (공고: {project.category} / 업체: {profile.business_type})"],
            )

        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=self.BUSINESS_TYPE_MISMATCH_PENALTY,
            reasons=[f"업무 구분이 달라 1차 업종 필터를 통과하지 못했습니다. (공고: {project.category} / 업체: {profile.business_type})"],
        )

    def _assess_license(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        """Evaluate whether the company holds the licenses explicitly required by the project."""
        required_licenses = self._extract_license_tokens(
            self._collect_project_text(project, include_title=False),
            require_context=True,
        )
        profile_licenses = self._extract_license_tokens(profile.license_codes)

        if not required_licenses:
            return RuleAssessment(
                score=self.LICENSE_NEUTRAL_SCORE,
                passed=True,
                reasons=["공고에 필수 면허 코드가 명시되지 않아 면허 조건은 중립 처리했습니다."],
            )

        if not profile_licenses:
            return RuleAssessment(
                score=0.0,
                passed=False,
                penalty=self.LICENSE_MISMATCH_PENALTY,
                reasons=[
                    f"공고 필수 면허({', '.join(sorted(required_licenses))})가 확인됐지만 업체 보유 면허 정보가 없습니다."
                ],
            )

        missing_licenses = required_licenses - profile_licenses
        if not missing_licenses:
            return RuleAssessment(
                score=self.LICENSE_MATCH_SCORE,
                passed=True,
                reasons=[f"필수 면허 코드를 모두 보유하고 있습니다: {', '.join(sorted(required_licenses))}."],
            )

        matched_licenses = required_licenses & profile_licenses
        matched_suffix = (
            f" 보유 면허 중 일치 항목: {', '.join(sorted(matched_licenses))}." if matched_licenses else ""
        )
        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=self.LICENSE_MISMATCH_PENALTY,
            reasons=[
                f"필수 면허 코드가 일부 부족합니다. 누락: {', '.join(sorted(missing_licenses))}.{matched_suffix}".strip()
            ],
        )

    def _assess_region(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        """Evaluate whether the company can serve the project's regions."""
        project_regions, has_strict_region_limit = self._extract_project_regions(project)
        profile_regions = self._extract_regions(profile.region_codes)

        if not project_regions or "전국" in project_regions:
            return RuleAssessment(
                score=self.REGION_NEUTRAL_SCORE,
                passed=True,
                reasons=["공고에 지역 제한이 명확하지 않거나 전국 대상으로 보여 지역 조건은 중립 처리했습니다."],
            )

        overlap = project_regions & profile_regions

        if not has_strict_region_limit:
            if "전국" in profile_regions or overlap:
                return RuleAssessment(
                    score=self.REGION_ADVISORY_SCORE,
                    passed=True,
                    reasons=[
                        f"공고에 언급된 지역({', '.join(sorted(project_regions))})과 업체 수행지역이 겹쳐 참고 가점을 반영했습니다."
                    ],
                )

            return RuleAssessment(
                score=self.REGION_NEUTRAL_SCORE,
                passed=True,
                reasons=["공고에 지역 언급은 있으나 명시적 제한으로 보지 않아 지역 불일치로 감점하지 않았습니다."],
            )

        if "전국" in profile_regions:
            return RuleAssessment(
                score=self.REGION_MATCH_SCORE,
                passed=True,
                reasons=[f"업체가 전국 대응 가능으로 등록되어 있어 제한지역({', '.join(sorted(project_regions))})을 충족합니다."],
            )

        if not profile_regions:
            return RuleAssessment(
                score=0.0,
                passed=False,
                penalty=self.REGION_MISMATCH_PENALTY,
                reasons=[f"공고 제한지역은 {', '.join(sorted(project_regions))}인데 업체 수행 지역 정보가 등록되어 있지 않습니다."],
            )

        if overlap:
            return RuleAssessment(
                score=self.REGION_MATCH_SCORE,
                passed=True,
                reasons=[f"수행 가능 지역이 공고 제한지역과 일치합니다: {', '.join(sorted(overlap))}."],
            )

        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=self.REGION_MISMATCH_PENALTY,
            reasons=[
                f"공고 제한지역({', '.join(sorted(project_regions))})과 업체 수행지역({', '.join(sorted(profile_regions))})이 일치하지 않습니다."
            ],
        )

    def _assess_budget(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        """Evaluate whether the company appears to have enough financial capacity."""
        budget_estimate = float(project.budget_estimate or 0.0)
        annual_revenue = float(profile.annual_revenue or 0.0)
        capacity_score = self._normalize_capacity_score(profile.capacity_score)
        construction_capacity_amount = float(
            getattr(profile, "construction_capacity_amount", 0.0) or 0.0
        )
        awarded_contract_limit = float(
            getattr(profile, "awarded_contract_limit", 0.0) or 0.0
        )
        project_type = self._normalize_business_type(project.category)

        if budget_estimate <= 0:
            return RuleAssessment(
                score=0.0,
                passed=True,
                reasons=["공고 예산 정보가 없어 예산 적합도는 참고 점수에서 제외했습니다."],
            )

        # Construction-only HARD CEILING: 도급한도(awarded_contract_limit) is a
        # legal cap on a single award — exceeding it means 신규 수주 불가 regardless
        # of how strong 시공능력평가액 is. This must fail the budget axis BEFORE the
        # 시공능력 ratio branch can pass it. When 도급한도 is not provided (<= 0) the
        # check is skipped entirely so all existing behaviour is preserved.
        if (
            project_type == "construction"
            and awarded_contract_limit > 0
            and budget_estimate > awarded_contract_limit
        ):
            return RuleAssessment(
                score=0.0,
                passed=False,
                penalty=self.BUDGET_MISMATCH_PENALTY,
                reasons=[
                    f"공고 예산({budget_estimate:,.0f}원)이 업체 도급한도({awarded_contract_limit:,.0f}원)를 "
                    f"초과해 신규 수주가 불가합니다."
                ],
            )

        # Construction-only: when the operator has filled in 시공능력평가액 it
        # is the canonical 도급가능규모 indicator and outranks annual_revenue
        # /capacity_score. The legacy fallbacks below run unchanged when the
        # field is left at its default (0.0).
        if project_type == "construction" and construction_capacity_amount > 0:
            capacity_ratio = construction_capacity_amount / budget_estimate
            capacity_label = f"시공능력평가액 {construction_capacity_amount:,.0f}원"
            if capacity_ratio >= 3:
                return RuleAssessment(
                    score=self.BUDGET_STRONG_SCORE,
                    passed=True,
                    reasons=[
                        f"{capacity_label}이 공고 예산의 {capacity_ratio:.1f}배 수준으로 도급 가능 규모가 충분합니다."
                    ],
                )
            if capacity_ratio >= 1:
                return RuleAssessment(
                    score=self.BUDGET_GOOD_SCORE,
                    passed=True,
                    reasons=[
                        f"{capacity_label}이 공고 예산 이상으로 시공능력 기준 필터를 충족합니다. (배수: {capacity_ratio:.1f})"
                    ],
                )
            if capacity_ratio >= 0.6:
                return RuleAssessment(
                    score=self.BUDGET_BORDERLINE_SCORE,
                    passed=True,
                    reasons=[
                        f"{capacity_label}이 공고 예산 대비 다소 타이트하지만 시공능력 기준으로 보수적 통과 처리했습니다. (배수: {capacity_ratio:.1f})"
                    ],
                )

            return RuleAssessment(
                score=0.0,
                passed=False,
                penalty=self.BUDGET_MISMATCH_PENALTY,
                reasons=[
                    f"{capacity_label}이 공고 예산 대비 부족해 시공능력 기준 필터를 통과하지 못했습니다. (배수: {capacity_ratio:.1f})"
                ],
            )

        if annual_revenue > 0:
            revenue_ratio = annual_revenue / budget_estimate
            if revenue_ratio >= 3:
                return RuleAssessment(
                    score=self.BUDGET_STRONG_SCORE,
                    passed=True,
                    reasons=[f"연매출이 공고 예산의 {revenue_ratio:.1f}배 수준으로 예산 대응 여력이 충분합니다."],
                )
            if revenue_ratio >= 1:
                return RuleAssessment(
                    score=self.BUDGET_GOOD_SCORE,
                    passed=True,
                    reasons=[f"연매출이 공고 예산 이상으로 기본 예산 필터를 충족합니다. (배수: {revenue_ratio:.1f})"],
                )
            if revenue_ratio >= 0.6:
                return RuleAssessment(
                    score=self.BUDGET_BORDERLINE_SCORE,
                    passed=True,
                    reasons=[f"연매출이 공고 예산 대비 다소 타이트하지만 1차 수행 가능 범위로 판단했습니다. (배수: {revenue_ratio:.1f})"],
                )

            return RuleAssessment(
                score=0.0,
                passed=False,
                penalty=self.BUDGET_MISMATCH_PENALTY,
                reasons=[f"연매출이 공고 예산 대비 낮아 예산 적합도 필터를 통과하지 못했습니다. (배수: {revenue_ratio:.1f})"],
            )

        if capacity_score >= 0.8:
            return RuleAssessment(
                score=self.CAPACITY_HIGH_SCORE,
                passed=True,
                reasons=[f"연매출 정보는 없지만 capacity_score={capacity_score:.2f}로 높아 예산 적합도에 보수적 가점을 반영했습니다."],
            )

        if capacity_score >= 0.4:
            return RuleAssessment(
                score=self.CAPACITY_BASE_SCORE,
                passed=True,
                reasons=[f"연매출 정보가 없어 capacity_score={capacity_score:.2f} 기준으로 예산 적합도를 보수 평가했습니다."],
            )

        return RuleAssessment(
            score=0.0,
            passed=True,
            reasons=["연매출 및 수행역량 정보가 부족해 예산 적합도는 보수적으로 평가했습니다."],
        )

    def _assess_capability(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        """Evaluate whether the company's operational capability seems adequate for the project size."""
        available_capability = self._estimate_company_capability(profile)
        required_capability = self._estimate_required_capability(project)

        if available_capability >= required_capability + 0.15:
            return RuleAssessment(
                score=self.CAPABILITY_STRONG_SCORE,
                passed=True,
                reasons=[
                    f"업체 수행능력(capacity_score/실적 반영 {available_capability:.2f})이 요구 수준({required_capability:.2f})보다 충분히 높습니다."
                ],
            )

        if available_capability >= required_capability:
            return RuleAssessment(
                score=self.CAPABILITY_GOOD_SCORE,
                passed=True,
                reasons=[
                    f"업체 수행능력(capacity_score/실적 반영 {available_capability:.2f})이 요구 수준({required_capability:.2f})을 충족합니다."
                ],
            )

        if available_capability + 0.1 >= required_capability:
            return RuleAssessment(
                score=self.CAPABILITY_BORDERLINE_SCORE,
                passed=True,
                reasons=[
                    f"업체 수행능력이 요구 수준({required_capability:.2f})에 근접해 보수적으로 통과 처리했습니다. 현재 추정치: {available_capability:.2f}."
                ],
            )

        return RuleAssessment(
            score=0.0,
            passed=False,
            penalty=self.CAPABILITY_MISMATCH_PENALTY,
            reasons=[
                f"업체 수행능력(capacity_score/실적 반영 {available_capability:.2f})이 요구 수준({required_capability:.2f})보다 낮아 수행 범위 필터를 통과하지 못했습니다."
            ],
        )

    def _assess_semantic_similarity(self, project: Project, profile: CompanyProfile) -> RuleAssessment:
        """Blend semantic similarity between project text and company capability summary."""
        if not settings.ENABLE_SEMANTIC_CLASSIFICATION:
            return RuleAssessment(
                score=0.0,
                passed=True,
                reasons=["의미 기반 분류가 비활성화되어 규칙 기반 점수만 사용했습니다."],
            )

        project_text = self._build_project_semantic_text(project)
        profile_text = self._build_profile_semantic_text(profile)
        similarity, source = self._compute_semantic_similarity(project_text, profile_text)
        source_label = "임베딩 모델" if source == "sentence-transformers" else "fallback 유사도"
        threshold = settings.CLASSIFIER_SEMANTIC_MATCH_THRESHOLD
        has_semantic_context = self._has_enough_semantic_context(project_text, profile_text)

        if similarity >= threshold + 0.2:
            return RuleAssessment(
                score=self.SEMANTIC_STRONG_SCORE,
                passed=True,
                reasons=[f"공고와 업체 역량 요약의 의미 유사도가 매우 높습니다. ({source_label}: {similarity:.2f})"],
            )

        if similarity >= threshold:
            return RuleAssessment(
                score=self.SEMANTIC_GOOD_SCORE,
                passed=True,
                reasons=[f"공고 내용과 업체 역량의 의미 유사도가 양호합니다. ({source_label}: {similarity:.2f})"],
            )

        if similarity >= max(0.0, threshold - 0.12):
            return RuleAssessment(
                score=self.SEMANTIC_BASE_SCORE,
                passed=True,
                reasons=[f"의미 유사도가 보통 수준으로 확인되어 보수적 가점을 반영했습니다. ({source_label}: {similarity:.2f})"],
            )

        if similarity <= 0.03:
            return RuleAssessment(
                score=0.0,
                passed=not has_semantic_context,
                penalty=self.SEMANTIC_VERY_LOW_PENALTY,
                reasons=[f"의미 유사도가 매우 낮아 규칙 기반 점수의 false positive 가능성을 줄이기 위해 {'차단 신호와 ' if has_semantic_context else ''}큰 감점을 반영했습니다. ({source_label}: {similarity:.2f})"],
            )

        if similarity <= 0.1:
            return RuleAssessment(
                score=0.0,
                passed=True,
                penalty=self.SEMANTIC_LOW_PENALTY,
                reasons=[f"의미 유사도가 낮아 최종 추천 점수를 보수적으로 낮췄습니다. ({source_label}: {similarity:.2f})"],
            )

        return RuleAssessment(
            score=0.0,
            passed=True,
            reasons=[f"의미 유사도는 중간 이하 수준이어서 추가 가점 없이 유지했습니다. ({source_label}: {similarity:.2f})"],
        )

    def _normalize_business_type(self, value: str | None) -> str | None:
        """Map raw business types to a stable canonical value."""
        if not value:
            return None

        normalized = value.strip().lower()
        for canonical, aliases in self.BUSINESS_TYPE_ALIASES.items():
            if normalized == canonical:
                return canonical
            if any(alias in normalized for alias in aliases):
                return canonical

        return normalized

    def _extract_regions(self, text: str | None) -> set[str]:
        """Extract normalized Korean region names from free-form text."""
        if not text:
            return set()

        found_regions = set()
        normalized_text = str(text)
        for canonical, aliases in self.REGION_ALIASES.items():
            if any(alias in normalized_text for alias in aliases):
                found_regions.add(canonical)
        return found_regions

    def _extract_project_regions(self, project: Project) -> tuple[set[str], bool]:
        """Extract project regions and whether they appear as a strict participation limit."""
        project_text = self._collect_project_text(project)
        project_regions = self._extract_regions(project_text)
        has_strict_limit = any(keyword in project_text for keyword in self.REGION_RESTRICTION_KEYWORDS)
        return project_regions, has_strict_limit

    def _collect_project_text(self, project: Project, include_title: bool = True) -> str:
        """Collect project fields into a single searchable text blob."""
        fields = [project.description, project.requirements]
        if include_title:
            fields.insert(0, project.title)
        return " ".join(filter(None, fields))

    def _build_project_semantic_text(self, project: Project) -> str:
        """Build a semantic text summary for the project."""
        project_type = self._normalize_business_type(project.category)
        project_regions, _ = self._extract_project_regions(project)
        project_licenses = self._extract_license_tokens(self._collect_project_text(project), require_context=False)
        parts = [self._collect_project_text(project)]

        if project.category:
            parts.append(f"공고업종 {project.category}")
        if project_type:
            parts.append("업종동의어 " + " ".join(sorted(self.BUSINESS_TYPE_ALIASES.get(project_type, {project_type}))))
        if project_regions:
            parts.append("지역요건 " + " ".join(sorted(project_regions)))
        if project_licenses:
            parts.append("필수면허 " + " ".join(sorted(project_licenses)))
        parts.append(self._describe_project_scope(project))

        return " ".join(part for part in parts if part)

    def _extract_license_tokens(self, text: str | None, require_context: bool = False) -> set[str]:
        """Extract normalized license tokens from text or stored company profile data."""
        if not text:
            return set()

        raw_text = str(text)
        normalized_text = raw_text.upper()
        extracted = {match.upper() for match in self.LICENSE_CODE_PATTERN.findall(normalized_text)}

        if not require_context or any(keyword in raw_text for keyword in self.LICENSE_CONTEXT_KEYWORDS):
            lowered_text = raw_text.lower()
            for canonical, aliases in self.LICENSE_ALIASES.items():
                if any(alias.lower() in lowered_text for alias in aliases):
                    extracted.add(canonical)

        # NOTE on substring nesting: alias matching is plain substring matching,
        # so a longer construction-license name extracts the shorter one too —
        # e.g. "실내건축공사업"(INT001) and "토목건축공사업"(CIVARC001) both contain
        # "건축공사업"(ARC001). When a notice and a profile use the same long name,
        # both sides extract the same superset and the comparison stays symmetric
        # (still matches correctly). A profile holding ONLY the bare "건축공사업"
        # vs. a notice requiring "실내건축공사업" correctly mismatches (INT001 missing
        # from the profile). This recall-first behaviour is covered by the
        # `_assess_license` nesting tests rather than special-cased here.
        return extracted

    def _build_profile_semantic_text(self, profile: CompanyProfile) -> str:
        """Build a semantic text summary for the company profile."""
        profile_type = self._normalize_business_type(profile.business_type)
        profile_licenses = self._extract_license_tokens(profile.license_codes)
        profile_regions = self._extract_regions(profile.region_codes)
        parts = [f"업체업종 {profile.business_type}"]

        if profile_type:
            parts.append("업종동의어 " + " ".join(sorted(self.BUSINESS_TYPE_ALIASES.get(profile_type, {profile_type}))))
        if profile_licenses:
            parts.append("보유면허 " + " ".join(sorted(profile_licenses)))
        if profile_regions:
            parts.append("수행지역 " + " ".join(sorted(profile_regions)))
        parts.append(self._describe_company_capability(profile))

        return " ".join(part for part in parts if part)

    def _describe_project_scope(self, project: Project) -> str:
        """Describe project scale and complexity in text form for semantic matching."""
        required_capability = self._estimate_required_capability(project)
        if required_capability >= 0.85:
            scale = "대형 복합 프로젝트"
        elif required_capability >= 0.65:
            scale = "중대형 프로젝트"
        elif required_capability >= 0.5:
            scale = "중형 프로젝트"
        else:
            scale = "기본 프로젝트"

        return f"{scale} 요구역량 {required_capability:.2f}"

    def _describe_company_capability(self, profile: CompanyProfile) -> str:
        """Describe company capability as text for semantic matching."""
        available_capability = self._estimate_company_capability(profile)
        annual_revenue = float(profile.annual_revenue or 0.0)
        awards = int(profile.total_awards or 0)

        if annual_revenue >= 1_000_000_000:
            revenue_band = "대형 프로젝트 대응 가능"
        elif annual_revenue >= 300_000_000:
            revenue_band = "중대형 프로젝트 대응 가능"
        elif annual_revenue > 0:
            revenue_band = "중형 프로젝트 대응 가능"
        else:
            revenue_band = "매출 정보 부족"

        if awards >= 5:
            award_band = "낙찰 실적 다수"
        elif awards >= 2:
            award_band = "낙찰 실적 보유"
        else:
            award_band = "낙찰 실적 제한적"

        return f"업체역량 {available_capability:.2f} {revenue_band} {award_band}"

    def _compute_semantic_similarity(self, project_text: str, profile_text: str) -> tuple[float, str]:
        """Compute semantic similarity using sentence-transformers with a lexical fallback."""
        if not project_text.strip() or not profile_text.strip():
            return 0.0, "fallback-empty"

        if settings.ENVIRONMENT != "test":
            model = self._get_embedding_model()
            if model is not None:
                embeddings = model.encode([project_text, profile_text], normalize_embeddings=True)
                similarity = float(embeddings[0] @ embeddings[1])
                return max(0.0, min(1.0, similarity)), "sentence-transformers"

        return self._fallback_semantic_similarity(project_text, profile_text), "fallback-lexical"

    @classmethod
    def _get_embedding_model(cls):
        """Load and cache the sentence-transformers model when available."""
        model_name = settings.CLASSIFIER_EMBEDDING_MODEL
        if cls._embedding_model is not None and cls._embedding_model_name == model_name:
            return cls._embedding_model
        if cls._embedding_model_failed and cls._embedding_model_name == model_name:
            return None

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

    def _fallback_semantic_similarity(self, project_text: str, profile_text: str) -> float:
        """Fallback lexical similarity used in tests or offline environments."""
        project_tokens = self._tokenize_semantic_text(project_text)
        profile_tokens = self._tokenize_semantic_text(profile_text)

        if not project_tokens or not profile_tokens:
            return 0.0

        project_token_set = set(project_tokens)
        profile_token_set = set(profile_tokens)
        overlap = project_token_set & profile_token_set
        similarity = (2 * len(overlap)) / (len(project_token_set) + len(profile_token_set))
        return round(max(0.0, min(1.0, similarity)), 4)

    def _tokenize_semantic_text(self, text: str) -> list[str]:
        """Tokenize semantic text into comparable units for the fallback scorer."""
        raw_tokens = re.findall(r"[A-Z]{2,}\d{2,}|[A-Za-z]{2,}|[가-힣]{2,}", text.upper())
        normalized_tokens: list[str] = []
        for token in raw_tokens:
            normalized_token = token.strip().lower()
            if len(normalized_token) < 2:
                continue
            normalized_tokens.append(normalized_token)
        return normalized_tokens

    def _has_enough_semantic_context(self, project_text: str, profile_text: str) -> bool:
        """Return whether both sides have enough tokens to treat a very low similarity as a blocker."""
        return (
            len(set(self._tokenize_semantic_text(project_text))) >= 4
            and len(set(self._tokenize_semantic_text(profile_text))) >= 4
        )

    def _estimate_company_capability(self, profile: CompanyProfile) -> float:
        """Estimate company execution capability using profile score and delivery history."""
        capacity_score = self._normalize_capacity_score(profile.capacity_score)
        award_bonus = min(0.25, max(0, int(profile.total_awards or 0)) * 0.03)
        return min(1.0, max(capacity_score, capacity_score + award_bonus))

    def _estimate_required_capability(self, project: Project) -> float:
        """Estimate the capability level a project likely requires."""
        project_budget = max(float(project.budget_estimate or 0.0), float(project.budget_max or 0.0))
        if project_budget >= 500_000_000:
            required = 0.85
        elif project_budget >= 200_000_000:
            required = 0.7
        elif project_budget >= 100_000_000:
            required = 0.55
        else:
            required = 0.4

        project_text = self._collect_project_text(project)
        required += min(0.15, sum(0.03 for keyword in self.COMPLEXITY_KEYWORDS if keyword in project_text))
        return min(1.0, required)

    def _normalize_capacity_score(self, value: float | None) -> float:
        """Normalize capacity scores that may come in either 0-1 or 0-100 scales."""
        if value is None:
            return 0.0

        normalized = float(value)
        if normalized > 1:
            normalized /= 100.0

        return max(0.0, min(1.0, normalized))
