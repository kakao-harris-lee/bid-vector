"""Declarative field routing table for Telegram strategy edits.

``FieldSpec`` folds the previously duplicated alias→type routing (parsing in
``_handle_set``/``_parse_assignment_updates`` and application in
``_apply_updates``) into a single source of truth: one spec per stored strategy
field carries its parser, its ORM applier, and every alias that maps to it.
``parser`` receives the entered (normalized) alias so numeric/boolean error
messages and clamping keep referencing the exact key the operator typed.

Extracted from ``telegram_strategy.py`` (kept re-exported there for the import
contract). The stateless parse/apply helpers delegate to
``telegram_strategy_parsing`` so the routing table shares one leaf-parser
implementation with the command orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    join_multi_value_text,
)
from app.services.notifications import telegram_strategy_parsing as parsing
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    dump_category_priority_overrides,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.notifications.telegram_strategy import (
        TelegramStrategyCommandProcessor,
    )


FieldParser = Callable[["TelegramStrategyCommandProcessor", str, str], Any]
FieldApplier = Callable[[Any, str, Any], None]


@dataclass(frozen=True)
class FieldSpec:
    """Declarative parse/apply routing for one stored strategy field."""

    target_field: str
    parser: FieldParser
    applier: FieldApplier
    aliases: tuple[str, ...]


def _parse_list_value(processor: "TelegramStrategyCommandProcessor", raw_value: str, normalized_key: str) -> Any:
    return parsing.parse_list(raw_value)


def _parse_number_value(processor: "TelegramStrategyCommandProcessor", raw_value: str, normalized_key: str) -> Any:
    return parsing.parse_number(raw_value, field_name=normalized_key)


def _parse_bool_value(processor: "TelegramStrategyCommandProcessor", raw_value: str, normalized_key: str) -> Any:
    return parsing.parse_bool(raw_value, field_name=normalized_key)


def _parse_overrides_value(processor: "TelegramStrategyCommandProcessor", raw_value: str, normalized_key: str) -> Any:
    return parsing.parse_priority_overrides(raw_value)


def _apply_list_field(strategy: Any, field_name: str, value: Any) -> None:
    setattr(strategy, field_name, join_multi_value_text(value))


def _apply_overrides_field(strategy: Any, field_name: str, value: Any) -> None:
    strategy.category_priority_overrides = dump_category_priority_overrides(value)


def _apply_workload_field(strategy: Any, field_name: str, value: Any) -> None:
    strategy.auto_workload_penalty_multiplier = clamp_auto_workload_penalty_multiplier(float(value))


def _apply_max_candidates_field(strategy: Any, field_name: str, value: Any) -> None:
    strategy.max_recommended_candidates = max(1, min(int(value), 100))


def _apply_plain_field(strategy: Any, field_name: str, value: Any) -> None:
    setattr(strategy, field_name, value)


_FIELD_SPEC_LIST: tuple[FieldSpec, ...] = (
    FieldSpec("focus_categories", _parse_list_value, _apply_list_field, ("categories", "category", "focus_categories")),
    FieldSpec("focus_regions", _parse_list_value, _apply_list_field, ("regions", "region", "focus_regions")),
    FieldSpec("exclude_regions", _parse_list_value, _apply_list_field, ("exclude_regions",)),
    FieldSpec("required_keywords", _parse_list_value, _apply_list_field, ("required_keywords", "keywords", "keyword")),
    FieldSpec("exclude_keywords", _parse_list_value, _apply_list_field, ("exclude_keywords",)),
    FieldSpec("min_budget_estimate", _parse_number_value, _apply_plain_field, ("min_budget", "min_budget_estimate")),
    FieldSpec("max_budget_estimate", _parse_number_value, _apply_plain_field, ("max_budget", "max_budget_estimate")),
    FieldSpec(
        "minimum_match_score",
        _parse_number_value,
        _apply_plain_field,
        ("match", "min_match", "minimum_match_score"),
    ),
    FieldSpec(
        "minimum_probability_score",
        _parse_number_value,
        _apply_plain_field,
        ("probability", "min_probability", "minimum_probability_score"),
    ),
    FieldSpec("bid_now_threshold", _parse_number_value, _apply_plain_field, ("bid_now", "bid_now_threshold")),
    FieldSpec("review_threshold", _parse_number_value, _apply_plain_field, ("review", "review_threshold")),
    FieldSpec(
        "auto_workload_penalty_multiplier",
        _parse_number_value,
        _apply_workload_field,
        ("workload_penalty", "auto_workload_penalty_multiplier"),
    ),
    FieldSpec(
        "max_recommended_candidates",
        _parse_number_value,
        _apply_max_candidates_field,
        ("limit", "max_candidates", "max_recommended_candidates"),
    ),
    FieldSpec(
        "notify_only_high_priority",
        _parse_bool_value,
        _apply_plain_field,
        ("high_priority", "high_priority_only", "notify_only_high_priority"),
    ),
    FieldSpec(
        "category_priority_overrides",
        _parse_overrides_value,
        _apply_overrides_field,
        ("category_priority_overrides", "priority_overrides"),
    ),
)

# Keyed by canonical stored field (drives ``apply_updates``).
FIELD_SPECS: Mapping[str, FieldSpec] = {spec.target_field: spec for spec in _FIELD_SPEC_LIST}
# Reverse index alias → spec (drives ``/strategy_set`` and step-flow parsing).
FIELD_SPEC_BY_ALIAS: Mapping[str, FieldSpec] = {
    alias: spec for spec in _FIELD_SPEC_LIST for alias in spec.aliases
}


# Button-edit metadata: label/example/help per inline-keyboard field.
EDIT_FIELDS: dict[str, dict[str, str]] = {
    "categories": {
        "label": "업종",
        "example": "software,security",
        "help": "쉼표로 구분해 관심 업종을 입력하세요. 비우려면 clear를 입력하세요.",
    },
    "regions": {
        "label": "지역",
        "example": "서울특별시,경기도",
        "help": "쉼표로 구분해 관심 지역을 입력하세요. 비우려면 clear를 입력하세요.",
    },
    "keywords": {
        "label": "키워드",
        "example": "AI,데이터",
        "help": "쉼표로 구분해 필수 키워드를 입력하세요. 비우려면 clear를 입력하세요.",
    },
    "budget": {
        "label": "예산",
        "example": "90000000 180000000",
        "help": "최소/최대 예산을 공백으로 입력하세요. 또는 min_budget=90000000 max_budget=180000000 형식을 사용할 수 있습니다.",
    },
    "thresholds": {
        "label": "임계치",
        "example": "match=0.65 probability=0.60 bid_now=0.75 review=0.50",
        "help": "0~1 사이 값으로 적합/확률/즉시투찰/검토 임계치를 입력하세요.",
    },
    "notification": {
        "label": "알림 범위",
        "example": "high_priority 또는 all",
        "help": "고우선순위만 받으려면 high_priority, 모든 후보를 받으려면 all을 입력하세요.",
    },
    "limit": {
        "label": "후보 수",
        "example": "10",
        "help": "전략 모니터링에서 추천할 최대 후보 수를 1~100 사이 숫자로 입력하세요.",
    },
}

# Clear-command groups: normalized key → stored fields reset to defaults.
CLEAR_GROUPS: dict[str, tuple[str, ...]] = {
    "categories": ("focus_categories",),
    "category": ("focus_categories",),
    "regions": ("focus_regions",),
    "region": ("focus_regions",),
    "exclude_regions": ("exclude_regions",),
    "keywords": ("required_keywords",),
    "keyword": ("required_keywords",),
    "exclude_keywords": ("exclude_keywords",),
    "budget": ("min_budget_estimate", "max_budget_estimate"),
    "thresholds": ("minimum_match_score", "minimum_probability_score", "bid_now_threshold", "review_threshold"),
    "priority_overrides": ("category_priority_overrides",),
    "high_priority": ("notify_only_high_priority",),
    "limit": ("max_recommended_candidates",),
    "all": (
        "focus_categories",
        "focus_regions",
        "exclude_regions",
        "required_keywords",
        "exclude_keywords",
        "min_budget_estimate",
        "max_budget_estimate",
        "minimum_match_score",
        "minimum_probability_score",
        "bid_now_threshold",
        "review_threshold",
        "auto_workload_penalty_multiplier",
        "category_priority_overrides",
        "notify_only_high_priority",
        "max_recommended_candidates",
    ),
}


def apply_updates(strategy: Any, updates: dict[str, Any]) -> None:
    """Persist parsed update values onto the ORM object."""
    for field_name, value in updates.items():
        spec = FIELD_SPECS.get(field_name)
        if spec is None:
            setattr(strategy, field_name, value)
        else:
            spec.applier(strategy, field_name, value)


def validate_updates(strategy: Any, updates: dict[str, Any]) -> str | None:
    """Validate score ranges and cross-field threshold ordering."""
    numeric_ranges = {
        "min_budget_estimate": (0.0, None),
        "max_budget_estimate": (0.0, None),
        "minimum_match_score": (0.0, 1.0),
        "minimum_probability_score": (0.0, 1.0),
        "bid_now_threshold": (0.0, 1.0),
        "review_threshold": (0.0, 1.0),
        "auto_workload_penalty_multiplier": (0.0, 2.0),
        "max_recommended_candidates": (1, 100),
    }
    for field_name, (min_value, max_value) in numeric_ranges.items():
        if field_name not in updates:
            continue
        value = float(updates[field_name])
        if value < min_value:
            return f"{field_name} 값은 {min_value} 이상이어야 합니다."
        if max_value is not None and value > max_value:
            return f"{field_name} 값은 {max_value} 이하여야 합니다."

    next_bid_now = float(updates.get(
        "bid_now_threshold",
        strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    ))
    next_review = float(updates.get(
        "review_threshold",
        strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    ))
    if next_review > next_bid_now:
        return "review_threshold는 bid_now_threshold보다 클 수 없습니다."

    next_min_budget = float(updates.get("min_budget_estimate", strategy.min_budget_estimate or 0.0))
    next_max_budget = float(updates.get("max_budget_estimate", strategy.max_budget_estimate or 0.0))
    if next_min_budget > 0 and next_max_budget > 0 and next_min_budget > next_max_budget:
        return "min_budget은 max_budget보다 클 수 없습니다."
    return None


def default_value_for(field_name: str) -> Any:
    """Return the default value used when a Telegram clear command resets a field."""
    defaults: dict[str, Any] = {
        "focus_categories": [],
        "focus_regions": [],
        "exclude_regions": [],
        "required_keywords": [],
        "exclude_keywords": [],
        "min_budget_estimate": 0.0,
        "max_budget_estimate": 0.0,
        "minimum_match_score": 0.6,
        "minimum_probability_score": 0.55,
        "bid_now_threshold": DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
        "review_threshold": DEFAULT_OPERATOR_REVIEW_THRESHOLD,
        "auto_workload_penalty_multiplier": 1.0,
        "category_priority_overrides": {},
        "notify_only_high_priority": True,
        "max_recommended_candidates": 10,
    }
    return defaults[field_name]
