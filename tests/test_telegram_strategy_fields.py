"""Unit tests for the Telegram strategy FIELD_SPECS routing table.

These lock the single declarative field spec table that replaced the three
duplicated alias→type routing sites (parsing in ``_handle_set`` /
``_parse_assignment_updates`` and application in ``_apply_updates``). The
aliases, parser behaviour, applier behaviour, and Korean error strings must
stay byte-for-byte identical to the pre-refactor code.
"""

from types import SimpleNamespace

import pytest

from app.core.single_user import join_multi_value_text
from app.services.notifications.telegram_strategy import (
    FIELD_SPEC_BY_ALIAS,
    FIELD_SPECS,
    TelegramStrategyCommandProcessor,
)
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    dump_category_priority_overrides,
)

# Verbatim union of the pre-refactor LIST/NUMERIC/BOOL alias dicts plus the
# {"category_priority_overrides", "priority_overrides"} special case. This locks
# the reverse index so a dropped or remapped alias fails loudly.
EXPECTED_ALIAS_TARGETS = {
    "categories": "focus_categories",
    "category": "focus_categories",
    "focus_categories": "focus_categories",
    "regions": "focus_regions",
    "region": "focus_regions",
    "focus_regions": "focus_regions",
    "exclude_regions": "exclude_regions",
    "required_keywords": "required_keywords",
    "keywords": "required_keywords",
    "keyword": "required_keywords",
    "exclude_keywords": "exclude_keywords",
    "min_budget": "min_budget_estimate",
    "min_budget_estimate": "min_budget_estimate",
    "max_budget": "max_budget_estimate",
    "max_budget_estimate": "max_budget_estimate",
    "match": "minimum_match_score",
    "min_match": "minimum_match_score",
    "minimum_match_score": "minimum_match_score",
    "probability": "minimum_probability_score",
    "min_probability": "minimum_probability_score",
    "minimum_probability_score": "minimum_probability_score",
    "bid_now": "bid_now_threshold",
    "bid_now_threshold": "bid_now_threshold",
    "review": "review_threshold",
    "review_threshold": "review_threshold",
    "workload_penalty": "auto_workload_penalty_multiplier",
    "auto_workload_penalty_multiplier": "auto_workload_penalty_multiplier",
    "limit": "max_recommended_candidates",
    "max_candidates": "max_recommended_candidates",
    "max_recommended_candidates": "max_recommended_candidates",
    "high_priority": "notify_only_high_priority",
    "high_priority_only": "notify_only_high_priority",
    "notify_only_high_priority": "notify_only_high_priority",
    "category_priority_overrides": "category_priority_overrides",
    "priority_overrides": "category_priority_overrides",
}

# One valid raw value per target so each alias can be resolved through its parser.
_SAMPLE_RAW = {
    "focus_categories": "software,security",
    "focus_regions": "서울,경기",
    "exclude_regions": "부산",
    "required_keywords": "AI,데이터",
    "exclude_keywords": "제외",
    "min_budget_estimate": "1000",
    "max_budget_estimate": "2000",
    "minimum_match_score": "0.65",
    "minimum_probability_score": "0.6",
    "bid_now_threshold": "0.75",
    "review_threshold": "0.5",
    "auto_workload_penalty_multiplier": "1.2",
    "max_recommended_candidates": "10",
    "notify_only_high_priority": "true",
    "category_priority_overrides": "software:+0.1",
}


@pytest.fixture
def processor() -> TelegramStrategyCommandProcessor:
    return TelegramStrategyCommandProcessor()


@pytest.mark.parametrize("alias, target", sorted(EXPECTED_ALIAS_TARGETS.items()))
def test_alias_routes_to_expected_target(processor, alias, target):
    """Every alias must resolve to its canonical stored field via the reverse index."""
    resolved = processor._resolve_assignment(alias, _SAMPLE_RAW[target])
    assert resolved is not None
    assert resolved[0] == target
    assert FIELD_SPEC_BY_ALIAS[alias].target_field == target


def test_reverse_index_matches_expected_alias_targets():
    """The alias→spec index must equal the pre-refactor alias dicts verbatim."""
    assert {alias: spec.target_field for alias, spec in FIELD_SPEC_BY_ALIAS.items()} == EXPECTED_ALIAS_TARGETS


def test_field_specs_are_internally_consistent():
    """FIELD_SPECS (by target) and the alias reverse index must agree."""
    for target, spec in FIELD_SPECS.items():
        assert spec.target_field == target
        for alias in spec.aliases:
            assert FIELD_SPEC_BY_ALIAS[alias] is spec
    for alias, spec in FIELD_SPEC_BY_ALIAS.items():
        assert alias in spec.aliases
        assert spec.target_field in FIELD_SPECS


def test_parse_list_field(processor):
    updates = processor._parse_assignment_updates(["categories=software,security"])
    assert updates == {"focus_categories": ["software", "security"]}


def test_parse_numeric_threshold_is_float(processor):
    updates = processor._parse_assignment_updates(["match=0.65"])
    assert updates == {"minimum_match_score": 0.65}


def test_parse_numeric_limit_is_clamped_int(processor):
    assert processor._parse_assignment_updates(["limit=250"]) == {"max_recommended_candidates": 100}
    assert processor._parse_assignment_updates(["limit=0"]) == {"max_recommended_candidates": 1}


def test_parse_bool_field(processor):
    assert processor._parse_assignment_updates(["high_priority=off"]) == {"notify_only_high_priority": False}
    assert processor._parse_assignment_updates(["high_priority=true"]) == {"notify_only_high_priority": True}


def test_parse_priority_overrides_special_field(processor):
    updates = processor._parse_assignment_updates(["priority_overrides=software:+0.1,hardware:-0.2"])
    assert updates == {"category_priority_overrides": {"software": 0.1, "hardware": -0.2}}


def test_unknown_alias_error_message_is_byte_equal(processor):
    with pytest.raises(ValueError) as excinfo:
        processor._parse_assignment_updates(["bogus=1"])
    assert str(excinfo.value) == "지원하지 않는 항목: bogus"


def test_numeric_parse_error_uses_typed_alias(processor):
    """Numeric parse errors keep referencing the alias the operator typed."""
    with pytest.raises(ValueError) as excinfo:
        processor._parse_assignment_updates(["match=abc"])
    assert str(excinfo.value) == "match 값은 숫자여야 합니다."


def test_bool_parse_error_uses_typed_alias(processor):
    with pytest.raises(ValueError) as excinfo:
        processor._parse_assignment_updates(["high_priority=maybe"])
    assert str(excinfo.value) == "high_priority 값은 true 또는 false여야 합니다."


def test_handle_set_unknown_key_returns_help_verbatim(processor, test_db):
    """/strategy_set with an unknown key returns the help text with the exact error line."""
    result = processor._handle_set(test_db, ["bogus=1"])
    assert result == processor._build_help("지원하지 않는 항목: bogus")
    assert "처리 실패: 지원하지 않는 항목: bogus" in result


def test_apply_updates_routes_each_field_kind(processor):
    """Each applier kind must transform values exactly as the old if/elif chain did."""
    strategy = SimpleNamespace()
    updates = {
        "focus_categories": ["software", "security"],
        "category_priority_overrides": {"software": 0.1},
        "auto_workload_penalty_multiplier": 5.0,
        "max_recommended_candidates": 250,
        "minimum_match_score": 0.65,
        "notify_only_high_priority": True,
    }

    processor._apply_updates(strategy, updates)

    assert strategy.focus_categories == join_multi_value_text(["software", "security"])
    assert strategy.category_priority_overrides == dump_category_priority_overrides({"software": 0.1})
    assert strategy.auto_workload_penalty_multiplier == clamp_auto_workload_penalty_multiplier(5.0)
    assert strategy.max_recommended_candidates == 100
    assert strategy.minimum_match_score == 0.65
    assert strategy.notify_only_high_priority is True
