"""Resolve KONEPS 업종코드 → 도메인 그룹.

Group resolution is prefix-based and config-driven so domain experts can
adjust mappings via `settings.BUSINESS_GROUP_CODE_PREFIXES` without code
changes. Unknown or missing codes return None so callers can fall back to
the legacy `category` path.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings


def resolve_business_group(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    cleaned = str(code).strip()
    if not cleaned:
        return None
    prefixes = settings.BUSINESS_GROUP_CODE_PREFIXES or {}
    for group, prefix_list in prefixes.items():
        for prefix in prefix_list or []:
            if cleaned.startswith(prefix):
                return group
    return None
