"""Stateless string parsers for Telegram strategy commands.

Extracted from ``telegram_strategy.py`` so the leaf-level tokenizing/normalizing
helpers live in one place. These are pure functions — no ``Session`` and no
processor state — so the command orchestrator and the declarative
``FIELD_SPECS`` table can share them without duplication. Behaviour (including
the Korean error strings and clamping) is byte-for-byte identical to the
pre-extraction methods.
"""

from __future__ import annotations

import shlex


def split_tokens(text: str) -> list[str]:
    """Split command text while allowing quoted values."""
    try:
        return shlex.split((text or "").strip())
    except ValueError:
        return (text or "").strip().split()


def normalize_command(token: str) -> str:
    """Normalize Telegram slash commands that may include a bot username suffix."""
    command = token.strip().lower()
    if "@" in command:
        command = command.split("@", maxsplit=1)[0]
    return command


def parse_list(value: str) -> list[str]:
    """Parse comma or semicolon separated values."""
    if value.strip().lower() in {"", "-", "none", "null", "clear"}:
        return []
    normalized = value.replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def parse_number(value: str, *, field_name: str) -> float | int:
    """Parse a positive numeric field."""
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        raise ValueError(f"{field_name} 값은 숫자여야 합니다.") from None

    if field_name in {"limit", "max_candidates", "max_recommended_candidates"}:
        return max(1, min(int(parsed), 100))
    return parsed


def parse_bool(value: str, *, field_name: str) -> bool:
    """Parse a compact boolean option."""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "예", "네"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "아니오", "아니요"}:
        return False
    raise ValueError(f"{field_name} 값은 true 또는 false여야 합니다.")


def parse_priority_overrides(value: str) -> dict[str, float]:
    """Parse category priority adjustments like software:+0.1,hardware:-0.2."""
    if value.strip().lower() in {"", "-", "none", "null", "clear"}:
        return {}

    overrides: dict[str, float] = {}
    for item in value.replace(";", ",").split(","):
        if not item.strip():
            continue
        if ":" in item:
            category, raw_score = item.split(":", maxsplit=1)
        elif "=" in item:
            category, raw_score = item.split("=", maxsplit=1)
        else:
            raise ValueError("category_priority_overrides는 category:+0.1 형식이어야 합니다.")
        category = category.strip()
        if not category:
            continue
        try:
            overrides[category] = float(raw_score.strip())
        except ValueError:
            raise ValueError(f"{category} 보정값은 숫자여야 합니다.") from None
    return overrides


def parse_notification_scope(value: str) -> bool:
    """Parse notification range labels into notify_only_high_priority."""
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"high", "high_priority", "high_priority_only", "only", "고우선순위", "고우선순위만"}:
        return True
    if normalized in {"all", "all_candidates", "전체", "전체후보", "모두"}:
        return False
    return parse_bool(value, field_name="notification")


def format_list(values: list[str]) -> str:
    """Format a list for a compact Telegram status message."""
    return ", ".join(values) if values else "없음"
