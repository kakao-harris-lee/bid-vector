"""Shared stdlib-only helpers for repo scripts.

This module centralizes small CLI helpers that were previously duplicated
byte-for-byte across several ``scripts/*.py`` entrypoints. It imports only from
the standard library so it can be loaded as soon as the repo root is on
``sys.path`` (i.e. right after each script's bootstrap), without pulling in any
application dependencies.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, time

__all__ = ["parse_datetime", "positive_int"]


def parse_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) == 10:
        parsed_date = datetime.fromisoformat(normalized).date()
        parsed_time = time.max if end_of_day else time.min
        return datetime.combine(parsed_date, parsed_time, tzinfo=UTC)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed
