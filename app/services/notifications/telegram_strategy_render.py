"""Telegram strategy response rendering (status text, help, markup).

Extracted from ``telegram_strategy.py`` so the outbound message shape lives in
one place, separate from command orchestration and pending-edit persistence.
Every function is stateless: it takes an already-loaded ORM ``strategy`` (or
plain values) and returns text/markup. The processor keeps thin delegators so
the wording, ordering, and inline-keyboard layout stay byte-for-byte identical.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    split_multi_value_text,
)
from app.services.notifications.telegram_strategy_parsing import format_list
from app.services.operator_strategy_tuning import (
    get_strategy_category_priority_overrides,
)


@dataclass
class TelegramStrategyReply:
    """Outbound Telegram strategy response plus optional inline keyboard."""

    message: str
    reply_markup: dict[str, object] | None = None


def build_help(error_message: str | None) -> str:
    """Build command help, optionally prefixed with an error."""
    lines: list[str] = []
    if error_message:
        lines.append(f"처리 실패: {error_message}")
        lines.append("")
    lines.extend([
        "사용법:",
        "/strategy",
        "/strategy_set categories=software,security regions=서울 keywords=AI,데이터 min_budget=90000000 max_budget=180000000 match=0.65 probability=0.60 bid_now=0.75 review=0.50 high_priority=true limit=10",
        "/strategy_clear categories regions keywords budget thresholds",
    ])
    return "\n".join(lines)


def build_strategy_status(strategy: Any, *, include_help: bool) -> str:
    """Build a concise strategy summary suitable for Telegram."""
    lines = [
        "[ 입찰 전략 ]",
        f"관심 업종: {format_list(split_multi_value_text(strategy.focus_categories))}",
        f"관심 지역: {format_list(split_multi_value_text(strategy.focus_regions))}",
        f"제외 지역: {format_list(split_multi_value_text(strategy.exclude_regions))}",
        f"필수 키워드: {format_list(split_multi_value_text(strategy.required_keywords))}",
        f"제외 키워드: {format_list(split_multi_value_text(strategy.exclude_keywords))}",
        f"예산 범위: {float(strategy.min_budget_estimate or 0.0):,.0f} ~ {float(strategy.max_budget_estimate or 0.0):,.0f}",
        (
            "임계치: "
            f"적합 {float(strategy.minimum_match_score or 0.0):.2f}, "
            f"확률 {float(strategy.minimum_probability_score or 0.0):.2f}, "
            f"즉시투찰 {float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD):.2f}, "
            f"검토 {float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD):.2f}"
        ),
        f"고우선순위만 알림: {'예' if bool(strategy.notify_only_high_priority) else '아니오'}",
        f"최대 후보 수: {int(strategy.max_recommended_candidates or 10)}",
    ]
    overrides = get_strategy_category_priority_overrides(strategy)
    if overrides:
        lines.append(
            "카테고리 보정: "
            + ", ".join(f"{category}={value:+.2f}" for category, value in sorted(overrides.items()))
        )
    if include_help:
        lines.extend(["", build_help(None)])
    return "\n".join(lines)


def current_value_for(strategy: Any, field_key: str) -> str:
    """Format the current value for one button-edit field."""
    if field_key == "categories":
        return format_list(split_multi_value_text(strategy.focus_categories))
    if field_key == "regions":
        return format_list(split_multi_value_text(strategy.focus_regions))
    if field_key == "keywords":
        return format_list(split_multi_value_text(strategy.required_keywords))
    if field_key == "budget":
        return f"{float(strategy.min_budget_estimate or 0.0):,.0f} ~ {float(strategy.max_budget_estimate or 0.0):,.0f}"
    if field_key == "thresholds":
        return (
            f"적합 {float(strategy.minimum_match_score or 0.0):.2f}, "
            f"확률 {float(strategy.minimum_probability_score or 0.0):.2f}, "
            f"즉시투찰 {float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD):.2f}, "
            f"검토 {float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD):.2f}"
        )
    if field_key == "notification":
        return "고우선순위만" if bool(strategy.notify_only_high_priority) else "전체 후보"
    if field_key == "limit":
        return str(int(strategy.max_recommended_candidates or 10))
    return "확인 불가"


def format_updates(field_key: str, updates: dict[str, Any]) -> str:
    """Format staged updates for confirmation."""
    if field_key in {"categories", "regions", "keywords"}:
        value = next(iter(updates.values()))
        return format_list(value)
    if field_key == "budget":
        min_budget = updates.get("min_budget_estimate")
        max_budget = updates.get("max_budget_estimate")
        parts = []
        if min_budget is not None:
            parts.append(f"최소 {float(min_budget):,.0f}")
        if max_budget is not None:
            parts.append(f"최대 {float(max_budget):,.0f}")
        return ", ".join(parts)
    if field_key == "thresholds":
        labels = {
            "minimum_match_score": "적합",
            "minimum_probability_score": "확률",
            "bid_now_threshold": "즉시투찰",
            "review_threshold": "검토",
        }
        return ", ".join(f"{labels[key]} {float(value):.2f}" for key, value in updates.items())
    if field_key == "notification":
        return "고우선순위만" if updates.get("notify_only_high_priority") else "전체 후보"
    if field_key == "limit":
        return str(int(updates["max_recommended_candidates"]))
    return str(updates)


def build_strategy_edit_markup(build_callback_data: Callable[[str], str]) -> dict[str, object]:
    """Build the /strategy inline edit buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": "업종", "callback_data": build_callback_data("categories")},
                {"text": "지역", "callback_data": build_callback_data("regions")},
                {"text": "키워드", "callback_data": build_callback_data("keywords")},
            ],
            [
                {"text": "예산", "callback_data": build_callback_data("budget")},
                {"text": "임계치", "callback_data": build_callback_data("thresholds")},
            ],
            [
                {"text": "알림 범위", "callback_data": build_callback_data("notification")},
                {"text": "후보 수", "callback_data": build_callback_data("limit")},
            ],
        ],
    }


def build_apply_cancel_markup(
    build_callback_data: Callable[[str], str],
    apply_action: str,
    cancel_action: str,
) -> dict[str, object]:
    """Build confirmation buttons for a parsed step edit."""
    return {
        "inline_keyboard": [[
            {"text": "적용", "callback_data": build_callback_data(apply_action)},
            {"text": "취소", "callback_data": build_callback_data(cancel_action)},
        ]],
    }


def build_cancel_markup(
    build_callback_data: Callable[[str], str],
    cancel_action: str,
) -> dict[str, object]:
    """Build a single cancel button for value entry prompts."""
    return {
        "inline_keyboard": [[
            {"text": "취소", "callback_data": build_callback_data(cancel_action)},
        ]],
    }


def build_step_error_reply(
    example: str,
    current_value: str,
    error_message: str,
    cancel_markup: dict[str, object],
) -> TelegramStrategyReply:
    """Report validation errors without mutating the stored strategy."""
    return TelegramStrategyReply(
        "\n".join([
            f"처리 실패: {error_message}",
            f"현재 값: {current_value}",
            f"올바른 예시: {example}",
        ]),
        reply_markup=cancel_markup,
    )
