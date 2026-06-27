"""Pure project-matching / interpretation helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). They have no IO (``requests`` / Playwright ``page``), DB
(``Session`` / ``db.query`` / ``db.add``), or instance-state dependencies --
they operate only on plain dicts/lists, ``Project`` / request schemas,
stdlib URL utilities, and the already-extracted pure helpers in ``parsing``
-- so they live here as module-level pure functions to keep the collector
class focused on orchestration, DB persistence, and IO.

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite. To avoid an import cycle, this module must
never import ``collector``: the collector imports ``matching`` (and the
sibling ``parsing`` module), not the other way around. The DB-bound query /
persistence methods (``_find_matching_project`` / ``_resolve_project_for_item``
/ ``_resolve_tender_result`` / ``persist_crawl_results`` /
``_update_project_from_item``) remain in the collector and call into these
helpers.
"""

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from app.core.time import utc_now
from app.models.models import Project
from app.schemas.schemas import CrawlRequest
from app.services.koneps import parsing


def match_by_url_or_title(
    candidates: list[Project],
    *,
    target_source_url: str,
    target_title: str,
    target_agencies: set[str],
    target_budget: float,
    target_deadline: datetime | None,
) -> Project | None:
    """Resolve a project from a candidate set via source_url then scored title heuristics.

    Preserves the original matching priority (source_url > title) and the
    score-based best-match selection, but operates on a caller-supplied
    candidate list so the fast path can scope it to notice-less rows.
    """
    if target_source_url:
        for candidate in candidates:
            candidate_source_url = normalize_source_url(
                candidate.source_url or extract_project_source_url(candidate)
            )
            if candidate_source_url and candidate_source_url == target_source_url:
                return candidate

    if not target_title:
        return None

    best_candidate: Project | None = None
    best_score = -1
    for candidate in candidates:
        candidate_title = parsing.normalize_title(candidate.title)
        title_exact = candidate_title == target_title
        title_overlap = (
            not title_exact
            and bool(candidate_title)
            and (target_title in candidate_title or candidate_title in target_title)
        )
        if not title_exact and not title_overlap:
            continue

        budget_match = parsing.is_budget_compatible(candidate, target_budget)
        deadline_match = parsing.is_deadline_compatible(
            candidate.deadline, target_deadline
        )
        agency_overlap = len(extract_project_agency_keys(candidate) & target_agencies)

        matches = (
            (title_exact and agency_overlap > 0)
            or (title_exact and budget_match and deadline_match)
            or (
                title_overlap and agency_overlap > 0 and budget_match and deadline_match
            )
        )
        if not matches:
            continue

        score = (
            (6 if title_exact else 3)
            + (3 if agency_overlap > 0 else 0)
            + (2 if budget_match else 0)
            + (1 if deadline_match else 0)
        )
        if score > best_score:
            best_candidate = candidate
            best_score = score

    return best_candidate


def resolve_project_category(item: dict[str, Any], request: CrawlRequest) -> str:
    """Resolve the internal project category for a crawled notice."""
    request_category = str(request.category or "").strip().lower()
    if request_category and request_category not in {"general", "기타", "other"}:
        return request_category

    business_type = str(item.get("business_type") or "").strip().lower()
    category_map = {
        "소프트웨어": "software",
        "software": "software",
        "기술용역": "technical-service",
        "technical-service": "technical-service",
        "일반용역": "service",
        "service": "service",
        "general-service": "service",
        "물품": "goods",
        "goods": "goods",
        "공사": "construction",
        "construction": "construction",
    }
    return category_map.get(business_type, request_category or business_type or "other")


def resolve_budget_estimate(item: dict[str, Any]) -> float:
    """Prefer the most actionable estimate while falling back to the available base amount."""
    for value in (item.get("estimated_amount"), item.get("base_amount")):
        if value not in (None, "", 0, 0.0):
            return float(value)
    return 0.0


def resolve_project_status(item: dict[str, Any]) -> str:
    """Map crawl timing and opening metadata to an internal project lifecycle state."""
    item_metadata = item.get("metadata", {})
    status_text = " ".join(
        str(value or "")
        for value in (
            item_metadata.get("opening_status"),
            item_metadata.get("status"),
            item_metadata.get("opening_bid_classification"),
            item_metadata.get("opening_bid_progress_order"),
            item.get("title"),
        )
    )
    normalized_status_text = parsing.normalize_status_text(status_text)

    if any(
        keyword in normalized_status_text
        for keyword in ("취소", "공고취소", "입찰취소", "개찰취소", "정정취소")
    ):
        return "cancelled"
    if any(
        keyword in normalized_status_text
        for keyword in ("재공고", "재입찰", "재안내", "2차공고", "3차공고")
    ):
        return "re_notice"
    if any(
        keyword in normalized_status_text
        for keyword in ("유찰", "무응찰", "무투찰", "개찰불성립", "낙찰자없음")
    ):
        return "failed"
    if any(
        item_metadata.get(key)
        for key in (
            "winning_company",
            "winning_amount",
            "winning_rate",
            "opening_announced_at",
        )
    ):
        return "awarded"
    if any(keyword in normalized_status_text for keyword in ("낙찰", "계약완료")):
        return "awarded"
    if any(
        keyword in normalized_status_text
        for keyword in ("마감", "종료", "개찰완료", "개찰진행", "개찰대기")
    ):
        return "closed"

    closing_at = parsing.coerce_datetime(item.get("closing_at"))
    if closing_at is not None and closing_at <= utc_now():
        return "closed"
    return "open"


def normalize_source_url(value: Any) -> str:
    """Normalize a URL so detail links can be compared across formatting differences."""
    if not value:
        return ""
    parsed = urlparse(str(value).strip())
    if not parsed.scheme and not parsed.netloc:
        return str(value).strip().rstrip("/").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    normalized_url = f"{netloc}{path}".strip().lower()
    significant_query_pairs = [
        (key.strip().lower(), val.strip().lower())
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.strip().lower()
        in {
            "bidntceno",
            "bidntceord",
            "bidpbancno",
            "bidpbancord",
        }
    ]
    if significant_query_pairs:
        normalized_query = urlencode(sorted(significant_query_pairs))
        return f"{normalized_url}?{normalized_query}"
    return normalized_url


def extract_item_agency_keys(item: dict[str, Any]) -> set[str]:
    """Extract normalized agency names from a crawled notice payload."""
    item_metadata = item.get("metadata", {})
    return {
        normalized
        for normalized in (
            parsing.normalize_agency_name(item_metadata.get("issuing_agency")),
            parsing.normalize_agency_name(item_metadata.get("opening_demand_agency")),
            parsing.normalize_agency_name(item_metadata.get("demand_agency")),
        )
        if normalized
    }


def extract_project_agency_keys(project: Project) -> set[str]:
    """Extract normalized agency names from a stored project, including labeled notes."""
    agency_values = [project.issuing_agency, project.demand_agency]
    project_text = "\n".join(filter(None, [project.description, project.requirements]))
    for label in ("공고기관", "수요기관"):
        label_match = re.search(rf"{label}\s*[:：]\s*([^\n]+)", project_text)
        if label_match:
            agency_values.append(label_match.group(1).strip())

    return {
        normalized
        for normalized in (
            parsing.normalize_agency_name(value) for value in agency_values
        )
        if normalized
    }


def extract_project_notice_number(project: Project) -> str | None:
    """Read a notice number from explicit project metadata or free-form notes."""
    if project.notice_number:
        return project.notice_number

    project_text = "\n".join(filter(None, [project.description, project.requirements]))
    label_match = re.search(r"공고번호\s*[:：]\s*([A-Za-z0-9\-]+)", project_text)
    if label_match:
        return label_match.group(1).strip()
    return parsing.extract_notice_number(project_text)


def extract_project_source_url(project: Project) -> str | None:
    """Read a source URL from explicit project metadata or free-form notes."""
    if project.source_url:
        return project.source_url

    project_text = "\n".join(filter(None, [project.description, project.requirements]))
    url_match = re.search(r"https?://[^\s]+", project_text)
    if url_match:
        return url_match.group(0).strip()
    return None
