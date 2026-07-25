"""Pure text/value parsing primitives for the KONEPS collector.

These functions were extracted verbatim from
``KonepsCollectorService`` (``collector.py``). They have no IO, DB, or
instance-state dependencies, so they live here as module-level pure
functions to keep the collector class focused on orchestration.

Behavior is intentionally identical to the original methods; this module
is a pure relocation, not a rewrite.
"""

import re
from datetime import datetime
from typing import Any

from app.core.time import ensure_utc
from app.domain.rate_normalization import to_bid_rate_fraction
from app.models.models import Project


def safe_int(value: Any) -> int | None:
    """Convert optional OpenAPI count fields into integers."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_koneps_title(title_cell: Any) -> str:
    """Extract the visible KONEPS title from a result cell with optional state badges."""
    linked_title = title_cell.select_one(".link_txt")
    if linked_title:
        return linked_title.get_text(" ", strip=True)

    title_attr = title_cell.get("title")
    if title_attr:
        return title_attr.strip()

    return title_cell.get_text(" ", strip=True)


def extract_notice_number(text: str) -> str | None:
    """Extract a plausible notice number from freeform row text."""
    match = re.search(r"\b(?:\d{4,}-\d{2,}|\d{8,}|[A-Z]{2,}\d{2,})\b", text)
    return match.group(0) if match else None


def extract_amounts(text: str) -> list[float]:
    """Extract monetary values from text."""
    matches = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text)
    amounts = []
    for value in matches:
        try:
            amounts.append(float(value.replace(",", "")))
        except ValueError:
            continue
    return amounts


def extract_datetime(text: str) -> datetime | None:
    """Extract a datetime value from text when possible."""
    patterns = [
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y.%m.%d",
        "%Y/%m/%d",
    ]
    match = re.search(
        r"\d{4}[-./]\d{2}[-./]\d{2}(?:[T\s]+\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?",
        text,
    )
    if not match:
        return None

    raw = match.group(0)
    for pattern in patterns:
        try:
            return ensure_utc(datetime.strptime(raw, pattern))
        except ValueError:
            continue

    return None


def extract_title(
    cells: list[str], notice_number: str, link_text: str | None
) -> str | None:
    """Choose the most likely title cell."""
    generic_link_texts = {"상세", "상세보기", "보기", "조회", "바로가기"}
    if link_text and link_text.strip() not in generic_link_texts:
        return link_text.strip()

    for cell in cells:
        if cell == notice_number:
            continue
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", cell):
            continue
        if re.fullmatch(r"\d{4}[-./]\d{2}[-./]\d{2}(?:\s+\d{2}:\d{2})?", cell):
            continue
        return cell.strip()

    return None


def extract_region(cells: list[str]) -> str | None:
    """Extract a likely Korean region value."""
    region_keywords = [
        "서울",
        "부산",
        "대구",
        "인천",
        "광주",
        "대전",
        "울산",
        "세종",
        "경기",
        "강원",
        "충북",
        "충남",
        "전북",
        "전남",
        "경북",
        "경남",
        "제주",
        "전국",
    ]
    for cell in cells:
        for keyword in region_keywords:
            if keyword in cell:
                return keyword
    return None


def extract_license_codes(text: str) -> list[str]:
    """Extract structured license-like codes from row text."""
    return sorted(set(re.findall(r"\b[A-Z]{2,}\d{2,}\b", text)))


def extract_percentage(text: str) -> float | None:
    """Extract a percentage value from text."""
    if not text:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1))
    try:
        return float(str(text).strip())
    except ValueError:
        return None


def normalize_bid_rate_value(value: Any) -> float | None:
    """Normalize percentage-like bid rates into predictor-friendly ratios."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    else:
        percentage = extract_percentage(str(value))
        if percentage is not None:
            numeric = percentage
        else:
            try:
                numeric = float(str(value).replace(",", "").strip())
            except ValueError:
                return None
    if numeric <= 0:
        return None
    return round(float(to_bid_rate_fraction(numeric)), 6)


def coerce_int_value(value: Any) -> int | None:
    """Convert numeric text into an integer when possible."""
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def extract_integer_tokens(text: str, max_items: int | None = None) -> list[int]:
    """Extract integer tokens from text, optionally limiting the result length."""
    numbers = [int(token) for token in re.findall(r"\b\d{1,2}\b", text)]
    if max_items is None:
        return numbers
    return numbers[:max_items]


def find_field_value(field_map: dict[str, str], labels: list[str]) -> str:
    """Find the first matching field value using partial Korean label matching."""
    for label in labels:
        for key, value in field_map.items():
            if label in key:
                return value
    return ""


def coerce_amount(value: Any) -> float | None:
    """Convert arbitrary numeric text into a float amount when possible."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    amounts = extract_amounts(str(value))
    if amounts:
        return amounts[0]
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def coerce_datetime(value: Any) -> datetime | None:
    """Convert an arbitrary value into a datetime when possible."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value))
        except ValueError:
            return extract_datetime(value)
    return None


def normalize_status_text(value: Any) -> str:
    """Normalize crawl status text for keyword-based lifecycle mapping."""
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def merge_text_lines(existing: str | None, new_lines: list[str | None]) -> str:
    """Append unique crawl-derived text fragments while keeping any manual notes intact."""
    merged_lines = [
        line.strip()
        for line in str(existing or "").splitlines()
        if line and line.strip()
    ]
    merged_text = "\n".join(merged_lines)

    for line in new_lines:
        if not line:
            continue
        normalized_line = str(line).strip()
        if not normalized_line:
            continue
        if normalized_line in merged_lines:
            continue
        if normalized_line in merged_text:
            continue
        merged_lines.append(normalized_line)
        merged_text = "\n".join(merged_lines)

    return "\n".join(merged_lines)


def normalize_title(value: Any) -> str:
    """Normalize a notice title for strict duplicate detection."""
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").strip().lower())


def normalize_notice_number(value: Any) -> str:
    """Normalize notice numbers for direct key matching."""
    return re.sub(r"\s+", "", str(value or "").strip().upper())


def normalize_agency_name(value: Any) -> str:
    """Normalize agency names for cross-source matching."""
    return "".join(str(value or "").strip().lower().split())


def is_budget_compatible(project: Project, target_budget: float) -> bool:
    """Return whether an existing project's budget is close enough to a crawled notice."""
    if target_budget <= 0:
        return True

    candidate_budget = float(
        project.budget_estimate or project.budget_max or project.budget_min or 0.0
    )
    if candidate_budget <= 0:
        return True

    difference_ratio = abs(candidate_budget - target_budget) / max(
        candidate_budget, target_budget
    )
    return difference_ratio <= 0.15


def is_deadline_compatible(
    existing_deadline: datetime | None, target_deadline: datetime | None
) -> bool:
    """Return whether existing and crawled deadlines are close enough to represent the same notice."""
    if existing_deadline is None or target_deadline is None:
        return True
    return (
        abs(
            (
                ensure_utc(existing_deadline) - ensure_utc(target_deadline)
            ).total_seconds()
        )
        <= 60 * 60 * 24 * 7
    )


def should_replace_project_title(existing_title: str | None, new_title: Any) -> bool:
    """Prefer the crawled title only when the current one is missing or obviously synthetic."""
    existing = str(existing_title or "").strip()
    if not existing:
        return True
    return existing.startswith("KONEPS notice") or existing.startswith("KONEPS-")


def format_crawl_error_message(metadata: dict[str, Any]) -> str | None:
    """Return a compact crawl-job error message with live failure category context."""
    if not isinstance(metadata, dict):
        return None

    reason = metadata.get("fallback_reason")
    if not reason:
        return None

    live_failure = (
        metadata.get("live_failure")
        if isinstance(metadata.get("live_failure"), dict)
        else {}
    )
    stage = metadata.get("fallback_failure_stage") or live_failure.get("stage")
    category = metadata.get("fallback_failure_category") or live_failure.get("category")

    if stage or category:
        failure_label = "/".join(str(value) for value in (stage, category) if value)
        return f"[{failure_label}] {reason}"
    return str(reason)
