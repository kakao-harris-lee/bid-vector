"""Pure text / normalization helpers for the notice classifier.

Stateless functions (no scoring, no ``self``) extracted verbatim from
``NoticeClassifierService``: business-type / region / license normalization,
project-text collection, and the fallback tokenizer. All axis modules and the
service surface reuse these so normalization stays consistent.
"""

import re

from app.models.models import Project
from app.services.classification import taxonomy


def normalize_business_type(value: str | None) -> str | None:
    """Map raw business types to a stable canonical value."""
    if not value:
        return None

    normalized = value.strip().lower()
    for canonical, aliases in taxonomy.BUSINESS_TYPE_ALIASES.items():
        if normalized == canonical:
            return canonical
        if any(alias in normalized for alias in aliases):
            return canonical

    return normalized


def extract_regions(text: str | None) -> set[str]:
    """Extract normalized Korean region names from free-form text."""
    if not text:
        return set()

    found_regions = set()
    normalized_text = str(text)
    for canonical, aliases in taxonomy.REGION_ALIASES.items():
        if any(alias in normalized_text for alias in aliases):
            found_regions.add(canonical)
    return found_regions


def collect_project_text(project: Project, include_title: bool = True) -> str:
    """Collect project fields into a single searchable text blob."""
    fields = [project.description, project.requirements]
    if include_title:
        fields.insert(0, project.title)
    return " ".join(filter(None, fields))


def extract_project_regions(project: Project) -> tuple[set[str], bool]:
    """Extract project regions and whether they appear as a strict participation limit."""
    project_text = collect_project_text(project)
    project_regions = extract_regions(project_text)
    has_strict_limit = any(
        keyword in project_text for keyword in taxonomy.REGION_RESTRICTION_KEYWORDS
    )
    return project_regions, has_strict_limit


def extract_license_tokens(text: str | None, require_context: bool = False) -> set[str]:
    """Extract normalized license tokens from text or stored company profile data."""
    if not text:
        return set()

    raw_text = str(text)
    normalized_text = raw_text.upper()
    extracted = {match.upper() for match in taxonomy.LICENSE_CODE_PATTERN.findall(normalized_text)}

    if not require_context or any(keyword in raw_text for keyword in taxonomy.LICENSE_CONTEXT_KEYWORDS):
        lowered_text = raw_text.lower()
        for canonical, aliases in taxonomy.LICENSE_ALIASES.items():
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


def tokenize_semantic_text(text: str) -> list[str]:
    """Tokenize semantic text into comparable units for the fallback scorer."""
    raw_tokens = re.findall(r"[A-Z]{2,}\d{2,}|[A-Za-z]{2,}|[가-힣]{2,}", text.upper())
    normalized_tokens: list[str] = []
    for token in raw_tokens:
        normalized_token = token.strip().lower()
        if len(normalized_token) < 2:
            continue
        normalized_tokens.append(normalized_token)
    return normalized_tokens


def has_enough_semantic_context(project_text: str, profile_text: str) -> bool:
    """Return whether both sides have enough tokens to treat a very low similarity as a blocker."""
    return (
        len(set(tokenize_semantic_text(project_text))) >= 4
        and len(set(tokenize_semantic_text(profile_text))) >= 4
    )


def normalize_capacity_score(value: float | None) -> float:
    """Normalize capacity scores that may come in either 0-1 or 0-100 scales."""
    if value is None:
        return 0.0

    normalized = float(value)
    if normalized > 1:
        normalized /= 100.0

    return max(0.0, min(1.0, normalized))
